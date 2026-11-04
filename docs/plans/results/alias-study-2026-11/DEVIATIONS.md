# Deviations from the frozen pre-registration: alias study (OA-1000)

The pre-registration is frozen at commit `e6ecfa6a3175b454601b6c119fc1a4a2351161e9`
(`prereg/PREREG-alias-study.md`, sha256 `d54f4201a4d1281dd895adeca292162999b55109e518129c87f61af473e0822e`,
frozen 2026-11-04T05:39:34Z; signed tag `prereg-alias-study-2026-11`). The file is never edited. Every
change after the freeze is recorded here, dated, stating what changed, why, when, what had been seen at
that time and which results it touches (prereg §12). A result whose computation depends on an entry says
so in its sentence.

## D-1, 2026-11-04: the R6 inputs and three declared sensitivity lists were built after the stage-A analysis ran

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

**What was done.** `python/scripts/eval/alias_study_inputs.py` (with tests), run at `59cc6a4` from a
clean tree (`citable true`), 2026-11-04T22:33Z. It reads only the frozen record set, the pilots',
calibration and validation build logs, the v4 corpus manifests (pinned by `counts-v4.json`), the v4 arm
chunk files, the base chunk export and the stored embeddings. Every read passes an allow-list guard that
refuses the judge's directory, every analysis directory, the run rows, the spot checks and the pilot's
judge and report, whatever the allow-list says. No judge label, retrieval row, found@10, statistics output
or spot-check label is read, and no external call is made. The script asserts the prereg's counts and
stops on a mismatch. It built:

| input | definition (source) | count |
|---|---|---|
| declared subset: calibration overlap | balanced record ids that are calibration record ids drafted by the main run (§4.3 item 5) | 2 records |
| declared subset: pilot papers | satisfied papers with a draft in either pilot's draft log (§4.3 item 4) | 13 papers |
| declared subset: validation papers | satisfied papers with a judge-validation draft (§4.3 item 6, O10) | 8 papers, 24 balanced records |
| topic | the study work's OpenAlex primary topic, as the v4 neighbour-selection manifest records it (§5.1) | 300 papers, 230 topics |
| citation count | the study work's OpenAlex `cited_by_count` in the same manifest (§5.1; equal to the first build's) | 300 papers |
| text similarity | mean cosine of the stored embeddings (the served embedder) over every pair of a study-paper chunk and a chunk of its attributed neighbours | 298 papers; 2 have no neighbour |
| XML-only | every attributed neighbour and every paper of both random draws for the study paper is JATS-sourced | 145 papers |
| added chunks | per arm, the chunks of the papers added for the study paper, from the v4 chunk files (equal to `per-study-chunks-v4.json` wherever it lists the paper) | 300 papers |
| neighbour attribution | added neighbour paper → its study paper (§5.1, O5) | 2,423 papers |

**The tercile cut points** are `artifacts/tercile-cuts.json` (sha256
`0aac771db34c163bab47f1c8bb032acb4bb2356fcf3c2f354c5753dfeb4d4c80`): citation count **[10, 33]**
(terciles of 101 / 100 / 99 papers), text similarity **[0.7096, 0.7362]** (100 / 99 / 99 of 298; the file
holds the unrounded values). The private outputs and their `SHA256SUMS` (sha256
`57181179f7c8c7983f922c724f8ef9147309684ac00eee8e5420d5bc54dba71e`) are kept with the study data.

**The cut points were fixed after the stage-A outputs had been read** by the session that ran the
stage-A analysis. That is exactly what §7 forbids. They are computed by a fixed rule from pre-outcome
covariates, and the rule reads no outcome. That does not make them pre-registered. The text similarity,
likewise, was computed after an outcome had been read, not before as §7 requires.

**What was not fixed by the prereg, and was chosen by the builder.** The prereg leaves five details open.
Each was settled from the definitions above without reading any outcome, and each is stated in the
script's `DEFINITIONS` and in the output files.

1. **The quantile convention for the cut points.** c1 and c2 are the ⌈n/3⌉-th and ⌈2n/3⌉-th ascending
   values (the inverse empirical CDF), matching the statistics' rule T1: v ≤ c1, T2: c1 < v ≤ c2,
   T3: v > c2. Another convention could move the citation-count cuts, which are integers with ties.
2. **Text similarity as a mean over all chunk pairs.** It is not a mean of per-neighbour means, so a
   neighbour with more chunks weighs more.
3. **XML-only for a study paper with no added paper** (2 papers): vacuously true, since no PDF text enters
   its contrast.
4. **Added chunks for those 2 papers are 0** in every arm, so the volume covariate uses all 300 papers.
   §7's x̄ / s_x of 0.216 was quoted over the 298 with added papers, and the builder reproduces it there
   (0.2164).
5. **The citation count is the study work's own record.** The manifest also records a count with each
   paper's citer list, which differs on 11 of the 300 papers.

**What had been seen.** The analysing session had read the stage-A statistics output before these
inputs were defined or built. The building session read no stage-A outcome before its outputs were
written. After they were written, while looking for this file's format, it opened the committed public
artifact `artifacts/stage-a-commit.public.json`, which holds found@10 and found@10-gold counts per stage-A
leg, pooled over all records and not split by paper or tercile.

**Which results it touches.** The declared subset sensitivities, the fixed sensitivities of R6, and the
*fragile* reading of H1 and H2. It does not touch the primary tests of H1, H1b and H2, their labels or
gates, or Layer 2. The stage-A statistics are to be re-run with these inputs (the config keys are written
beside the private outputs). Every sentence that quotes a tercile result says that its cut points were
fixed after the stage-A outputs had been read, and every sentence that quotes H1's or H2's *fragile*
reading cites this entry.
