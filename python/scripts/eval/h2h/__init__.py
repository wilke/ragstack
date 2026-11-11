"""Tooling for the adopted-spec head-to-head (``docs/plans/results/adopted-spec-h2h-2026-11/PREREG.md``).

``rules``          the decision sequence of PREREG §4.1, the §4.5 sentences under both cost
                   orders, H3 (§4.3) and the Holm family of §4.4 — pure functions on numbers;
``gates``          the file-checkable gates of §6 on receipts, worker stderr, store counts,
                   question-set hashes and re-embed cosines — pure functions on loaded data;
``report``         ``h2h_report.py``: reads the three-arm and five-arm score outputs per setting
                   plus a private manifest of gate inputs, computes everything, cross-checks
                   ``decision.json`` and writes the public ``artifacts/h2h-report.json``;
``dryrun_check``   the sentinel dry-run checks of the operational plan (§2 of the PREREG);
``reembed_sample`` the seeded 200-chunk re-embed sample (§2, §6) and its cosine check;
``make_configs``   the decision (3-arm) and reporting (5-arm) harness configs per setting,
                   derived key by key from the frozen Addendum C configs.

No module here calls a network service except ``reembed_sample fetch`` (run by the operator
at the freeze, never by the tests). Nothing writes a question, quote, record id, host or path
into a public file: :func:`report.assert_public` refuses.
"""
