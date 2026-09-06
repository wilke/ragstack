# R-dev pilot grading sheet — source

Published as a claude.ai artifact (`e1a4ac31-2c4b-44ff-a662-6a1ea3cfaa00`) on 2026-09-06.
`template.html` is the page with `__DATA__` where `pilot_data_r3.json` is inlined; the
build is a string replacement (escape `</script` in the JSON first). The data is the ten
pilot pairs from `../rdev_sample.json` (4 model-positive, 2 model-negative, 1 deep-section,
3 long-document; compendium outliers > 120k chars excluded) with the **union of both r3
judges' spans** (`../r3/labels-r3-{scout,qwen}.jsonl`), each set tagged by judge.

Verdicts are stored in the artifact's shared database under `pilot-r3/<reader>/verdicts/`
and exported as CSV in `s0_rdev_score.py`'s shape. Reader independence there is
honour-based; the RAGStack grading UI (`docs/plans/grading-ui.md`) enforces it server-side.
