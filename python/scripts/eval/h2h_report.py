#!/usr/bin/env python3
"""The §9.4 script of ``docs/plans/results/adopted-spec-h2h-2026-11/PREREG.md`` and its
companions, one command each (run with ``PYTHONPATH=python`` from the repo root, or from
``python/scripts/eval``):

    h2h_report.py report  --manifest M --frozen-inputs F --prereg PREREG.md --prereg-sha256 SHA --out artifacts/h2h-report.json
    h2h_report.py dryrun  --arm S --receipt R [--stderr-file F | --logs-json L] [...]
    h2h_report.py reembed select|fetch|verify ...
    h2h_report.py configs --addendum-c a=... d=... b=... c=... --out-root DIR [--public-diff P]

``report`` computes G1, H1, H2, H3, C1–C4, the strata, the sensitivity set, the drift and
overlap figures, the file-checkable gates, the cross-check against ``decision.json`` and
both cost orders' decisions, prints the §4.5 sentences and writes the public report
(aggregates only; it refuses to write a question, quote, record id, host or path).
"""
from __future__ import annotations

import sys
from pathlib import Path

_EVAL = Path(__file__).resolve().parent
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

from h2h import dryrun_check, make_configs, reembed_sample, report  # noqa: E402

COMMANDS = {"report": report.main, "dryrun": dryrun_check.main, "reembed": reembed_sample.main,
            "configs": make_configs.main}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    cmd = argv[0]
    if cmd not in COMMANDS:
        print(f"unknown command {cmd!r}; one of {', '.join(COMMANDS)}", file=sys.stderr)
        return 2
    return int(COMMANDS[cmd](argv[1:]))


if __name__ == "__main__":
    sys.exit(main())
