# Reach comparison: does the adopted chunking cost reach?

**Status: DRAFT pre-registration, not frozen.** This folder pre-registers a confirmatory comparison. On the
alias study's 1,091 questions, it compares retrieval under the fixed 512-token windows (W) and under the
adopted chunking (S): `sentence/512/0`, tokens, tolerance 0.25, section cuts, min_piece 128.

- **Families:**
  - **quote@10 (Q):** judge-free; the top 10 cover at least 90 % of the gold quote.
  - **judged found@10 (J):** a quote hit, or any top-10 chunk labelled *answers* by the frozen alias judge.
- **Primary test:** default retriever, pooled over four corpora, clustered by paper. Non-inferiority at
  δ = 3 points, then superiority.
- **Freeze procedure:** PREREG §9. At the freeze, this status line records the owner's go verbatim, the
  frozen commit, the PREREG sha256 and the UTC time.

| file | tier | what |
|---|---|---|
| [PREREG.md](PREREG.md) | record, once frozen | question, data, outcomes, tests, power, procedure, gates and decision wording, threats, freeze and deviations, code, owner decisions, cost, release |
| [power.py](power.py), [power.json](power.json) | record, once frozen | the seeded power simulation of the clustered design (standard library only), regenerated from a clean tree |
| [feasibility.py](feasibility.py), [feasibility.json](feasibility.json) | record, once frozen | W-side counts only: baselines, eligibility, judging load; no S-side outcome |
