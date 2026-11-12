# OA chunking eval: aggregate artifact

`oa-eval-aggregates.json` is the committed, public JSON record of the OA chunking eval's
numbers. It holds aggregates only. Use it as the `source` of any claim about this study
instead of the Markdown write-ups. `PLAN-AND-PASS-RULE.md` and
`POSTHOC-HYPOTHESES-2026-11-06.md` stay frozen as they are.

`make_aggregates.py` regenerates the artifact from the private experiments directory
(`oa-chunking-eval-2026-10/`, which holds `main-run-2026-11/` and `sections-2026-11/`):

```bash
PYTHONPATH=python /rag/envs/ragstack/bin/python \
  docs/plans/results/oa-chunking-eval-2026-10/artifacts/make_aggregates.py <private-dir>
```

Run it from a clean checkout so that `provenance.citable` is true. The script pins its
imports (`ragstack` and the `oa_chunk_eval` harness) to its own checkout by putting that
checkout first on `sys.path`, so provenance always describes the tree that holds the
script. Two runs on the
same inputs give the same output, except for `provenance.recorded_at`.

## What is in it

- `question_sets`: sample, passage, question and audit counts, plus the decision set
  (n = 2,035), the sensitivity set (2,335) and the decision set without the lossy strata
  (1,836).
- `construction`:
  - passage length and section types;
  - gold-quote length and the 64-token overlap width;
  - the share of quotes split by a chunk boundary (ft512o0, ft512o64, ft1024o0 only);
  - quotes crossing a section boundary (0 of 2,035);
  - chunk geometry measured in the stores;
  - the tercile cut points of the density proxies.
- `chunk_counts`: receipt chunk totals per arm for the full ingest.
- `noise_run`: per-setting noise results and the CI half-widths that set the frozen margins.
- `settings.<s>.arms.<arm>`: n, hit@1, hit@5 (with its 95% CI), hit@10, context tokens at
  k = 5 (chars/4 estimates), sensitivity-set hit@5 and a per-arm `status`. In settings
  (a)-(d) the six main-run arms are `confirmatory` and the four section arms `addendum-C`;
  in (e)-(f) every arm is `exploratory`. Settings: (a) hybrid + reranker
  (decision), (d) hybrid, (b) vector, (c) BM25; post-hoc (e) vector + reranker and
  (f) BM25 + reranker.
- `contrasts.<id>`: one entry per paired contrast. Each entry gives `pair`, `setting`, `set`,
  `n`, `point`, `lo`, `hi`, `method` (paired bootstrap, 10,000 resamples, seed 0, 95%
  percentile interval), `adjustment`, `status`, `family`, `source` and `recomputed`.
  Id prefixes:
  - `main_`: the frozen six-arm rule, status `confirmatory`. Challengers vs the baseline
    (decision and sensitivity sets), plus the paired-ladder steps.
  - `addC_`: Addendum C, status `addendum-C`. Contrasts C1-C4 (Holm k = 4), the step-2 tests
    against the baseline (Holm k = 3) and the step-3 replacement tests.
  - `ph_A_`, `ph_B_`, `ph_C_`, `ph_D_`: post-hoc hypotheses A-D, status `exploratory`. They
    include the subgroup contrasts by section type, length band, density tercile and echo
    tercile, and the split-quote subgroups (n = 119 and 1,916).
  - `rc_`: exploratory contrasts that no frozen file holds, computed here from the
    per-question files with the harness's own bootstrap (see below):
    - sempool512 and sem512 vs ft512o64s;
    - sempool512 vs ft512o64;
    - the main run's sempool512 vs sent512 head-to-head.
- `strata`: per-stratum contrasts vs the baseline for every arm and setting (journal family,
  length band, section type), with the stratum-guard outcome.
- `spread`: hypothesis C1/C2 spread statistics (SD across arms, best - worst, disagreement
  share, SD ratio with its bootstrap CI, pairwise slope/correlation, rank correlation and
  reranker gain range).
- `markdown_checks`: each listed Markdown figure compared with the entry it points at, at the
  printed precision. Mismatches are listed with an explanation. They are reported here, not
  corrected in either place.
- `sources_sha256`: the sha256 of every private file read, named by its path inside the
  private directory.
- `provenance`: `ragstack.provenance.experiment_provenance()`, with `host` dropped and
  warnings reduced to their codes.

## Copied vs recomputed

Most figures are copied from frozen outputs (`score.json`, `decision.json`, `noise.json`,
`addendum_c.json`, `posthoc/*.json`, data summaries). Those entries have
`recomputed: false`.

Entries with `recomputed: true` are the `rc_` contrasts, passage statistics, decision-set
stratum counts and receipt chunk totals. Before computing any new interval, the script
re-derives three frozen intervals with the same bootstrap. If they don't reproduce to 4 dp,
it refuses to write (`recompute_selfcheck`).

## Not covered

These figures were never saved to a private file, or depend on data outside the private
record, so they stay Markdown-only:

- the corpus article count (22,593);
- the split-quote shares for the six arms other than ft512o0, ft512o64 and ft1024o0 (those
  were measured against the live stores);
- the main-run store counts that include test chunks;
- the drift check;
- the planned 4.5-7x embedding cost of the legacy semantic arm.

## Hygiene

The artifact contains no question, quote or passage text, no record ids or PMCIDs, no
hostnames, ports or private paths, and no physical collection names.
