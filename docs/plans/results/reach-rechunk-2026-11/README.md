# Reach comparison: does the adopted chunking cost reach?

**Status: FROZEN 2026-11-10T17:38:07Z on the owner's go of 2026-11-10 ("Freeze and run retrieval"); S retrieval
started 2026-11-10T17:40:47Z, not yet reported.** `PREREG.md` is frozen as written at commit
`f78af6ee46e5d7f3604c10b1d14a77a7caa47b7d` (the merge of #989), sha256
`1e3791ede2b7252cf43b940fd24f99a40a4126cabfc3a698f9f3a3ba3ade4458` (its own status line, which still reads *DRAFT*, is part of the frozen text).
`power.json` sha256 `cfb6ddb49f894271623358b0b031626a279db7e1ebb5098581f64bcf2bf36276`; `feasibility.json` sha256
`d68f11f66ad0f002d6ccdcd0bc9c829bf549e190b1555dacecb171bf27868eb8`; `frozen-inputs.json` sha256
`35c24c2c6b4618c7c868352d004600344498c70c599ad0f0ebff7f8e7a6701a1`. Signed annotated tag
`prereg-reach-rechunk-2026-11` (verify with `git tag -v`), whose message also lists every §9.1 input sha256; the
sha256 and the UTC time are also in a private `PREREG.sha256`, written before the S retrieval (§9.2 step 2). The
live server config hash at the start of the S run equals W's (`2e91056f…`). This is the one freeze-record commit of
§9.2 step 4; the text below is kept as written at the freeze.

This folder pre-registers a confirmatory comparison. On the
alias study's 1,091 questions, it compares retrieval under the fixed 512-token windows (W) and under the
adopted chunking (S): `sentence/512/0`, tokens, tolerance 0.25, section cuts, min_piece 128.

- **Families** (owner decision, 2026-11-10: Q is the sole confirmatory family; PREREG §1, §11):
  - **quote@10 (Q), confirmatory:** judge-free; the top 10 cover at least 90 % of the gold quote.
  - **judged found@10 (J), descriptive:** a quote hit, or any top-10 chunk labelled *answers* by the frozen
    alias judge. Reported with the favourable bound and the unjudged counts; not tested, no gate.
- **Primary test (Q):** default retriever, pooled over four corpora, clustered by paper. Non-inferiority at
  δ = 3 points, then superiority.
- **Freeze procedure:** PREREG §9. At the freeze, this status line records the owner's go verbatim, the
  frozen commit, the PREREG sha256 and the UTC time.

| file | tier | what |
|---|---|---|
| [PREREG.md](PREREG.md) | record, once frozen | question, data, outcomes, tests, power, procedure, gates and decision wording, threats, freeze and deviations, code, owner decisions, cost, release |
| [power.py](power.py), [power.json](power.json) | record, once frozen | the seeded power simulation of the clustered design (standard library only), regenerated from a clean tree |
| [feasibility.py](feasibility.py), [feasibility.json](feasibility.json) | record, once frozen | W-side counts, baselines, eligibility (W-verbatim and S-reachable, from the S chunk texts), orphans, registry integrity, the prior-content check, the settings gate on real run summaries (W vs W, stage-1 S vs W) and judging load; no S-side outcome |
| [frozen-inputs.json](frozen-inputs.json) | record, once frozen | the §9.1 input pins as `reach_report.py report` checks them, generated from PREREG §9.1 (`reach_report.py frozen-inputs`) |

Note on the S run summaries: the harness's `rechunk` mode stamps every S `none.summary.json` with
`exploratory: True`. That stamp comes from the mode, which was written for the exploratory stage-1 runs.
Here the S summaries are **confirmatory input** to `reach_report.py`; the stamp does not change their
status, and the report's settings gate lists it among the declared expected differences (PREREG §7.1).
