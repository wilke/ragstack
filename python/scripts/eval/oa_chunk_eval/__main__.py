"""CLI: ``python -m oa_chunk_eval <cmd> --config C`` (run from python/scripts/eval with
PYTHONPATH pointing at python/, so ``ragstack`` imports from this checkout)."""
from __future__ import annotations

import argparse
import sys

from . import EXIT_CONFIG
from .common import log
from .config import ConfigError, load_config, out_path

COMMANDS = ("sample", "passages", "questions", "audit", "retrieve", "score", "decide", "noise")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="oa_chunk_eval", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in COMMANDS:
        p = sub.add_parser(c)
        p.add_argument("--config", required=True, help="JSON config (private; see examples/)")
        if c in ("questions", "retrieve"):
            p.add_argument("--reset-failures", action="store_true",
                           help="record a failure-count reset in the ledger(s) before running")
        if c == "retrieve":
            p.add_argument("--subset", choices=["noise"], default=None,
                           help="only the noise questions and the two noise arms")
            p.add_argument("--arm", action="append", default=None, help="only this arm (repeatable)")
        if c == "decide":
            p.add_argument("--force-unchecked", action="store_true",
                           help="decide even when score refusals, a noise.json margin mismatch or a missing "
                                "audit would refuse; the overridden checks are recorded in decision.json")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
        if getattr(args, "reset_failures", False):
            from .runner import reset_failures
            if args.cmd == "questions":
                reset_failures(out_path(cfg, "questions.ledger.jsonl"), "operator reset")
            else:
                from .config import arms
                from .retrieve import ledger_path
                for a in arms(cfg):
                    reset_failures(ledger_path(cfg, a["name"]), "operator reset")
        if args.cmd == "sample":
            from .sample import run
            return run(cfg)
        if args.cmd == "passages":
            from .passages import run
            return run(cfg)
        if args.cmd == "questions":
            from .questions import run
            return run(cfg)
        if args.cmd == "audit":
            from .audit import run
            return run(cfg)
        if args.cmd == "retrieve":
            from .retrieve import run
            return run(cfg, subset=args.subset, only_arms=args.arm)
        if args.cmd == "score":
            from .score import run
            return run(cfg)
        if args.cmd == "decide":
            from .decide import run
            return run(cfg, force_unchecked=args.force_unchecked)
        from .noise import run
        return run(cfg)
    except (ConfigError, FileNotFoundError, ValueError) as e:
        log(f"{args.cmd}: {type(e).__name__}: {e}")
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
