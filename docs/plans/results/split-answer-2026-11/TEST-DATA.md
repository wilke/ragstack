# How step 1's test data was made

Record file. Every step below is offline but the judging, and every published file holds counts only: no ids,
text, hosts or paths.

1. **Source pairs.** The confirmatory boundary test's stage-A pairs and its cover-any addendum
   ([`../alias-boundary-confirm-2026-11/`](../alias-boundary-confirm-2026-11/README.md)) give the lost 75 (split 18,
   recovered 53, primary_only 4) and the 219 kept_changed pairs, each with its max-overlap S chunk M (PLAN §3).
2. **Negative-control draw** (`split_answer_prep.py draw-negative`, PLAN §3.2 (b)). The seed
   [`artifacts/negative-draw-seed.json`](artifacts/negative-draw-seed.json) was committed before the draw, and the
   command refuses to run unless the seed file's bytes equal the committed ones. It draws 150 stage-A *not* pairs,
   stratified by corpus arm, about two-thirds same-paper and one-third other-paper, and only pairs whose M has
   neighbours on both sides. Quotas, candidates and exclusions per cell: [`artifacts/negative-draw.json`](artifacts/negative-draw.json).
3. **Neighbour items** (`split_answer_prep.py items`, PLAN §3.2 (a)). For each pair, the passage is built with
   production's own code path (link the chunk export's neighbours, `expand_context`, then
   `RagGenerator.format_context`), M ± 1, or M ± 2 for the 2 split windows M ± 1 does not contain. A walk that
   misses a neighbour is refused. Items get their own ids and their own registry, and the rechunk tooling refuses
   them as chunk labels. Counts per group and edge class: [`artifacts/neighbour-items.json`](artifacts/neighbour-items.json)
   (446, matching PLAN §5 (b)).
4. **Wall read** (`wall-read`, `wall-counts`, PLAN §3.2 (c)), before any neighbour label:
   [`artifacts/wall-read-counts.json`](artifacts/wall-read-counts.json).
5. **Judging.** The frozen alias-study judge on the 446-item registry: Qwen C (n = 5) and E locally, then the Argon
   tiebreak and the Opus drift reference on the gateway (DEVIATIONS D-1), then finalize.
6. **Report** (`split_answer_prep.py report`): reads the items and the final labels, writes
   [`artifacts/neighbour-report.json`](artifacts/neighbour-report.json) with the input files' sha256, makes no call.
