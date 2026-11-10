# Reach comparison: does the adopted chunking cost reach?

**Status: DRAFT pre-registration, not frozen.** This folder pre-registers a confirmatory comparison. On the
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
| [feasibility.py](feasibility.py), [feasibility.json](feasibility.json) | record, once frozen | W-side counts, baselines, eligibility (W-verbatim and S-reachable, from the S chunk texts), orphans, registry integrity and judging load; no S-side outcome |

Note on the S run summaries: the harness's `rechunk` mode stamps every S `none.summary.json` with
`exploratory: True`. That stamp comes from the mode, which was written for the exploratory stage-1 runs.
Here the S summaries are **confirmatory input** to `reach_report.py`; the stamp does not change their
status, and the report's settings gate lists it among the declared expected differences (PREREG §7.1).
