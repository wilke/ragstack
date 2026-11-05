# Deviations from the frozen pre-registration: alias study (OA-1000)

The pre-registration is frozen at commit `e6ecfa6a3175b454601b6c119fc1a4a2351161e9`
(`prereg/PREREG-alias-study.md`, sha256 `d54f4201a4d1281dd895adeca292162999b55109e518129c87f61af473e0822e`,
frozen 2026-11-04T05:39:34Z; signed tag `prereg-alias-study-2026-11`). The file is never edited. Every
change after the freeze is recorded here, dated, stating what changed, why, when, what had been seen at
that time and which results it touches (prereg §12). A result whose computation depends on an entry says
so in its sentence.

## D-1, 2026-11-04: the R6 inputs and three declared sensitivity lists were built after the stage-A analysis ran

Two sessions are named in this entry. The **analysing session** ran the stage-A analysis and read its
output; it also wrote the brief for this work. The **building session** wrote and ran the builder below.

**What the frozen text says.** §7 fixes two sets of sensitivities, printed beside the Layer-1 members.

- **The declared sensitivities** (§7): without the 2 calibration-overlap records (§4.3 item 5), without the
  13 satisfied papers that were pilot papers (§4.3 item 4), and without the 8 satisfied papers that carry
  judge-validation records (§4.3 item 6; §14 O10).
- **The fixed sensitivities of R6** (§7): topic-level clustering for H1 and H2, H2 with the chunk-volume
  covariate (the Freedman–Lane re-test of R23), H2 on the XML-only stratum, H2 by citation-count tercile
  and by text-similarity tercile, and the origin of displacing chunks. Of the text similarity, §7 says it
  is computed "offline from the stored vectors before any outcome is read"; of the terciles, "the tercile
  cut points are fixed and committed before the stage-A outputs are read".

**What happened.** The statistics script (`python/scripts/eval/alias_study_stats.py`, #913) implements all
of these, but reads their inputs from files its config names: `paper_covariates`, `tercile_cuts`,
`neighbour_attribution` and the `sensitivity_sets` lists. **No code built those files, and none was built
before the freeze or before the stage-A analysis.** §15 did not list them as an item of their own, so the
pre-freeze checklist passed without them, and the gap was not caught in review. The stage-A analysis ran
on 2026-11-04 (the script as at `812fea0`; its output directory was last written at 22:14Z). Without the
files, the script prints *absent* for:

- topic-level clustering, for H1 and for H2;
- H2's chunk-volume covariate (the Freedman–Lane re-test), the XML-only stratum and both tercile splits;
- the three declared subsets above;

and the origin counts lump every neighbour chunk as *attribution not supplied*. The bootstrap-t re-tests,
the studentized identity check, the Codex-verdict subset, the judge-mode and missing-data sensitivities
and H2 against each draw alone do not need these files and were computed.

**This touches more than displays.** By R14, topic clustering is one of H1's two re-tests behind
*fragile*, and topic clustering and the volume covariate are two of H2's three. These re-tests never change
a label (§7), but either can attach *fragile* to H1's or H2's verdict, and *fragile* is carried in every
sentence that quotes the verdict. **The stage-A output's *fragile* reading is therefore incomplete**: it
considered the bootstrap-t only. The other displays (the terciles, the XML-only stratum, the origin counts
and the three subsets) never make anything fragile and never decide anything.

**Decision (owner, 2026-11-04).** Build the missing inputs with a committed, reviewed script, mechanically
from pre-outcome sources, and re-run the stage-A statistics with them.

**Who defined what, and what each had seen.**

- **The building session made every input definition and every choice listed below, from the prereg's
  text** (§4.3, §5.1, §7) and the statistics script's input format. The building session read no stage-A
  outcome before its outputs were written.
- **The analysing session wrote the brief.** The brief named the missing inputs, the prereg sections to
  read and the rule that no outcome may be read, but it specified no definition and no value. The
  analysing session had seen the stage-A results before writing it. Those results include H2's contrast
  against each random draw, and which sensitivities were absent.
- **After writing its first outputs, the building session opened the committed public stage-A record**
  (`artifacts/stage-a-commit.public.json`) while looking for this file's format. That record holds pooled
  found@10 and found@10-gold counts per stage-A leg, and H2's pooled contrast can be derived from them. It
  holds nothing per paper or per tercile. The revisions after review (below) were made after that read.
  Of those revisions, only one changes a definition, and it moves to the prereg's own population (see
  *What the prereg's text decides*, below).
- **Binding rule.** The first statistics re-run with these inputs is the reported one. No definition or
  choice in this entry is re-chosen after it.

**What was done.** `python/scripts/eval/alias_study_inputs.py` reads only:

- the frozen record set;
- the pilots', calibration and validation build logs;
- the v4 corpus manifests (pinned by `counts-v4.json`) and the v4 arm chunk files;
- the base chunk export (pinned by its sha256);
- the stored embeddings (pinned by their `SHA256SUMS`).

Every read passes a guard that enforces four rules:

1. A path must be on a fixed allow-list exactly as given.
2. The path must contain no symlink, and it must resolve under the study root.
3. It must not lie under the judge's directory, any analysis directory, the run rows, the spot checks or the
   pilot's judge and report.
4. It must not carry the name of an outcome file (`final.jsonl`, `labels-*.jsonl`, `judge-summary.json`,
   `hits.jsonl`, `*.owner-labels.jsonl`, `stage-a-commit*.json`, `results-stage-*.json`), wherever it lies.

So no judge label, retrieval row, found@10, statistics output or spot-check label is read, and no external
call is made. The script asserts the prereg's counts and stops on any mismatch.

**History of the builder and its runs.**

- `59cc6a4`: the first run, from a clean tree (`citable true`).
- `a056b98`: changed only the script's docstring, replacing its usage line. The committed script at that
  commit behaves exactly as the one that generated the first outputs.
- `a83d520`: applies the review fixes:
  - the stricter guard;
  - the volume-covariate population (see *What the prereg's text decides*, below);
  - the open-choice checks;
  - the repo-relative cut path.

  The outputs were regenerated from a clean tree at `a83d520` (`citable true`), 2026-11-04. The first
  outputs are kept beside the new ones as superseded, and are never overwritten.
- This repository merges pull requests with merge commits, so `59cc6a4` stays reachable from `main`.

The inputs it built:

| input | definition (source) | count |
|---|---|---|
| declared subset: calibration overlap | balanced record ids that are calibration record ids drafted by the main run (§4.3 item 5) | 2 records |
| declared subset: pilot papers | satisfied papers with a draft in either pilot's draft log (§4.3 item 4) | 13 papers |
| declared subset: validation papers | satisfied papers with a judge-validation draft (§4.3 item 6, O10) | 8 papers, 24 balanced records |
| topic | the study work's OpenAlex primary topic, as the v4 neighbour-selection manifest records it (§5.1) | 300 papers, 230 topics |
| citation count | the study work's OpenAlex `cited_by_count` in the same manifest (§5.1; choice 5 below) | 300 papers |
| text similarity | mean cosine of the stored embeddings (the served embedder) over every pair of a study-paper chunk and a chunk of its attributed neighbours (§7) | 298 papers; 2 have no neighbour |
| XML-only | every attributed neighbour and every paper of both random draws for the study paper is JATS-sourced (§7) | 145 papers, of which 2 vacuously (143 with added papers) |
| added chunks | per arm, the chunks of the papers added for the study paper, from the v4 chunk files; equal to `per-study-chunks-v4.json` (§7, R23) | 298 papers; x̄ / s_x = 0.2164 |
| neighbour attribution | added neighbour paper → its study paper (§5.1, O5) | 2,423 papers |

**The tercile cut points** are in `artifacts/tercile-cuts.json` (sha256
`12153beacc9fb622164a24938d709fdf9d3f8376a3f04c0f6859b490c5058ba7`):

- **citation count [10, 33]**: terciles of 101 / 100 / 99 papers;
- **text similarity [0.7096, 0.7362]**: terciles of 100 / 99 / 99 of the 298 papers with a value (the file
  holds the unrounded values).

The cut values are unchanged from the first run. The private outputs are kept with the study data, with
their `SHA256SUMS` (sha256 `3df934e08a784efe94a83b5985bfb24efe4b5afcd5b5d75f3b2870b8d3a4318f`).

**The cut points were fixed after the stage-A outputs had been read** by the analysing session, which is
exactly what §7 forbids. They are computed by a fixed rule from pre-outcome covariates, and the rule reads
no outcome. That does not make them pre-registered. The text similarity, likewise, was computed after an
outcome had been read, not before as §7 requires.

**What the prereg's text decides.** Three readings follow §7's literal text, and the building session did
not choose them:

- **Text similarity is the mean over all chunk pairs.** It is not a mean of per-neighbour means, so a
  neighbour with more chunks weighs more.
- **The XML-only stratum includes the 2 study papers with no added paper.** For them, "all JATS-sourced"
  is vacuously true. Their H2 contrast compares two corpora that add nothing for them, so 143 of the 145
  papers carry added papers.
- **The volume covariate excludes those 2 papers.** They carry no added-chunk count, and the statistics
  leave them out (`papers_left_out`). This is the population over which §7 quotes x̄ / s_x = 0.216, and it
  gives 0.2164. The first run had given them 0 in every arm, putting them in. The review moved them out,
  and this is the one definition the review changed.

**What the prereg leaves open, and the building session chose.** Each choice is stated in the script's
definitions and in the output files, and the measurable ones were measured by the script.

1. **The quantile convention for the cut points.** c1 and c2 are the ⌈n/3⌉-th and ⌈2n/3⌉-th ascending
   values (inverse empirical CDF, type 1), matching the statistics' rule T1: v ≤ c1, T2: c1 < v ≤ c2,
   T3: v > c2. **Measured:** under the linear quantile (type 7), the cuts are the same, and **0 papers
   change tercile** for either variable.
2. **Null similarity for the 2 papers with no neighbour.** The similarity cuts therefore use n = 298, and
   the statistics count those 2 papers as without a value.
3. **The study paper's chunks come from the base export.** This is OA-1000 as indexed, the text the study
   retrieves from.
4. **PDF neighbours' reference-list chunks are included in the similarity.** They are in the neighbour
   collection as indexed (§5.1 notes that difference from the base text).
5. **The citation count is the study work's own record** (`cited_by_count`). §5.1 defines "OpenAlex
   citation count at query time" as the field the builder ranked references by, which is this field. The
   study works' records were fetched in the corpus build's OpenAlex lookup, logged from
   2026-11-01T21:42Z (the first manifest's query date), and the v4 manifest carries the same values. The
   manifest also records each study paper's citer-list total, a different query's count of citing works.
   That total differs on 11 of the 300 papers. **Measured:** with that count and its own cuts, the cuts
   are the same, and **0 papers change tercile**.

**Which results it touches.** The declared subset sensitivities, the fixed sensitivities of R6, and the
*fragile* reading of H1 and H2. It does not touch the primary tests of H1, H1b and H2, their labels or
gates, or Layer 2.

The stage-A statistics are to be re-run with these inputs; the config keys are written beside the private
outputs, and the cut points are read from the committed file. Every sentence that quotes a tercile result
says that its cut points were fixed after the stage-A outputs had been read. Every sentence that quotes
H1's or H2's *fragile* reading cites this entry.

## D-2, 2026-11-05: H1b's found@10-gold figure was computed afterwards, by a new script that decides nothing

**What the frozen text says.** §6.1: found@10-gold "is printed beside it everywhere"; §7 lists
found@10-gold among the figures printed beside every Layer-1 member; §3.4 gives H1b the same displays.

**What happened.** The statistics script (`python/scripts/eval/alias_study_stats.py`, at `15ee347` for both
the binding stage-A re-run of D-1 and the stage-B analysis) prints a gold-only net beside H1
(`gold_only_net_points`) and beside H2 (the gold-only direction gate), but **none beside H1b**. Its H1b
helpers already take the hit key as an argument; `layer1` calls them with the judged hit only. The gap was
not caught in review, and neither committed results file (`artifacts/results-stage-A2.public.json`,
`artifacts/results-stage-B.public.json`) carries the figure.

**Decision (2026-11-05).** Compute it afterwards, as a display, without re-running the statistics and
without changing any function of the statistics script (D-1's binding rule: the first re-run is the
reported one). A new committed script, `python/scripts/eval/alias_study_h1b_gold.py`, imports the
statistics module and calls its own loaders (`Data`, so the preflight, the analysis gate and the record-set
check run first), its H1b helpers (`h1b_label_supports`, `member_randomization`) and its seeds and counts,
with the gold-only hit vector (`hit_gold`) in place of the judged one. Same records, same cells, same
per-paper construction, same estimator. It reads the stage-B config, and before writing anything it
recomputes the judged H1b by the same path and requires it to equal the committed stage-B H1b exactly
(net, p, CI, counts), the H1 and H2 gold-only nets to equal theirs, and the input hashes to equal the
stage-B run's; otherwise it writes nothing. Its public output is `artifacts/h1b-gold.public.json`,
labelled *display only, decides nothing; computed post-hoc (DEVIATIONS D-2)*.

**What had been seen, and when.** Before this entry was written and before the figure was computed, the
stage-B results had been read: H1b's judged verdict (*confirmed*, net +1.3333 points, label-randomization
p 0.0293), H1's gold-only net (+5.5 points) and H2's gold-only net (−2.1667 points). The stage-B Layer-2
figures were also committed and had been read, including the gold-only G lift (+5.5) and the gold-only C
change (0.0) on the same base × default cells. Since every paper keeps its 2 G and 1 C records (none
missing), H1b's gold-only net is arithmetically the difference of those two, so its point estimate was
derivable from committed figures before the script ran; its CI and display p were not.

**Which results it touches.** None. The H1b gold-only figure enters no gate, no family procedure and no
verdict; H1b's label, p and level are the stage-B run's. The figure's p is a display figure of the same
label randomization, not a test. Every sentence that quotes H1b's gold-only figure cites this entry.

## Cross-reference, 2026-11-05: the chunk-boundary control is an addendum, not a deviation

The exploratory chunk-boundary control, with its cover-any step and its test-retest null, changes no
hypothesis, cell, record, judge, threshold, seed, gate or analysis of the frozen text. It is recorded in
[`ADDENDUM-2026-11-05-chunk-boundary-control.md`](ADDENDUM-2026-11-05-chunk-boundary-control.md) (plan:
`control/CONTROL-PLAN.md` §1).

## D-3, 2026-11-05: the §3.3 wording table has no row for a *not resolved* glossary reading beside H1 confirmed

**What the frozen text says.** §3.3's table of serving statements pairs each Layer-2 reading of the
glossary decision with the Layer-1 verdict. Its *not resolved* row covers only "H1 not confirmed or
GATE-NOT-EVALUABLE" ("the Layer-2 phrase, with the H1 verdict beside it"). No row covers *not resolved*
beside **H1 confirmed**.

**What happened.** Stage B's glossary decision reads *not resolved* (`results-stage-B.public.json`
`layer2.decisions.attach_merged_glossary.reading`). H1 and H1b were confirmed in stage A. The table
therefore gives no serving statement for this combination.

**Decision (owner, 2026-11-05).** `RESULTS.md` uses the nearest frozen rule: the Layer-2 phrase
*not resolved*, quoted as the artifact states it, with the Layer-1 verdicts (H1 confirmed, H1b confirmed)
beside it. Beside that statement, `RESULTS.md` quotes the frozen §3.2 reading for H1 confirmed × H1b
confirmed (an alias-specific gain, for questions using the glossary's primary term) and the §3.3 2-point
action threshold; no threshold or reading was changed.

**What had been seen, and when.** The stage-B results, including this reading and both Layer-1 verdicts,
had been read before this entry was written.

**Which results it touches.** Only the wording of the glossary serving statement in `RESULTS.md`. Every
figure, threshold and verdict is as computed.
