#!/usr/bin/env python
"""Alias study: the glossary step for the expanded arms' ADDED papers, run inside the pinned
tools image (prereg §5.3, *For added papers in every expanded arm*).

This file runs under the tools image's own Python and imports only the standard library, the
image's installed ``ragstack`` and the image's ``scripts/glossary_step.py``. It does not
re-implement the step: each work unit (one ``JsonlLoader`` shard written by
``alias_added_glossaries.py plan``) goes through the image script's own ``amain`` with the
argv the plan recorded, so the rows are what ``scripts/glossary_step.py`` at the image's
version writes.

What it adds around the step, and nothing else:

* **the served model id is checked on every reply** — ``OpenAILLM.complete_result`` is wrapped;
  a reply whose ``model`` is not the requested id writes ``STOP`` beside the ledger and exits
  the process at once (exit 42), before the answer reaches the step;
* **at most ``--units`` units in flight** (each with ``--llm-concurrency 1``, and the step's
  calls within a document are sequential), so at most that many model calls are in flight;
* **resumable**: a unit whose output file exists and verifies is skipped, so a restart never
  re-asks a finished document (a long document is a unit of its own; short documents, which
  make no call, are grouped per paper);
* a ledger line per finished unit (fsynced) with the reply count and the served ids seen.

Nothing here reads the environment except the API-key variable the step itself reads (it is
unset: the endpoint takes no key). No store, workflow or gateway is touched.
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import hashlib
import importlib.util
import json
import logging
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

log = logging.getLogger("alias_added_worker")
STOP_EXIT = 42
#: the running unit's reply counter (tasks the step creates inherit the context, so every
#: reply of a unit's calls lands in that unit's counter even with several units in flight)
UNIT_REPLIES: contextvars.ContextVar[Counter[str] | None] = contextvars.ContextVar("unit_replies", default=None)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _load_step_script(path: str) -> Any:
    spec = importlib.util.spec_from_file_location("glossary_step_script", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load the step script at {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ServedCheck:
    """Wraps ``OpenAILLM.complete_result``: every reply's ``model`` must be ``expected``."""

    def __init__(self, expected: str, stop_file: Path) -> None:
        self.expected = expected
        self.stop_file = stop_file
        self.seen: Counter[str] = Counter()

    def install(self) -> None:
        from ragstack import llm as llm_mod

        orig = llm_mod.OpenAILLM.complete_result
        check = self

        async def complete_result(self_: Any, *a: Any, **k: Any) -> Any:
            res = await orig(self_, *a, **k)
            served = getattr(res, "model", "")
            check.seen[served] += 1
            unit = UNIT_REPLIES.get()
            if unit is not None:
                unit[served] += 1
            if served != check.expected:
                msg = {"stop": "served_model_mismatch", "requested": check.expected, "served": served,
                       "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                check.stop_file.write_text(json.dumps(msg) + "\n", encoding="utf-8")
                print(json.dumps(msg), file=sys.stderr, flush=True)
                os._exit(STOP_EXIT)
            return res

        llm_mod.OpenAILLM.complete_result = complete_result  # type: ignore[method-assign]


def _verify_unit_output(out: Path, want_ids: list[str]) -> tuple[bool, str]:
    """The unit's file holds exactly ``want_ids`` (header + one row each)."""
    from ragstack.ingestion.glossary_file import GlossaryFileError, read_glossary_file

    try:
        gf = read_glossary_file(out)
    except (GlossaryFileError, OSError) as e:
        return False, f"unreadable: {e}"
    got = sorted(gf.rows)
    if got != sorted(want_ids):
        return False, f"doc ids differ: {len(got)} rows vs {len(want_ids)} expected"
    return True, "ok"


async def run(args: argparse.Namespace) -> int:
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    work = Path(plan["work_dir"])
    stop_file = work / "STOP"
    if stop_file.exists():
        print(f"STOP present ({stop_file}); remove it only after reading it", file=sys.stderr)
        return STOP_EXIT
    step = _load_step_script(plan["step_script"])
    check = ServedCheck(plan["model"], stop_file)
    check.install()
    units = plan["units"]
    if args.only:
        only = set(Path(args.only).read_text().split())
        units = [u for u in units if u["unit"] in only]
    if args.limit:
        units = units[: args.limit]
    ledger = open(work / "ledger.jsonl", "a", encoding="utf-8")
    todo = []
    skipped = 0
    for u in units:
        out = Path(u["out"])
        if out.exists():
            ok, why = _verify_unit_output(out, u["doc_ids"])
            if ok:
                skipped += 1
                continue
            log.warning("unit %s: existing output does not verify (%s); re-running", u["unit"], why)
            out.rename(out.with_suffix(out.suffix + f".bad-{int(time.time())}"))
        todo.append(u)
    log.info("units: %d planned, %d already done, %d to run", len(units), skipped, len(todo))
    sem = asyncio.Semaphore(args.units)
    done = 0
    failed = 0
    t0 = time.monotonic()

    async def one(u: dict[str, Any]) -> None:
        nonlocal done, failed
        async with sem:
            if stop_file.exists():
                return
            replies: Counter[str] = Counter()
            UNIT_REPLIES.set(replies)
            argv = [u["shard"], "--out", u["out"], "--summary", u["summary"], *plan["step_argv"]]
            t = time.monotonic()
            rc = await step.amain(step.parse_args(argv))
            ok, why = (False, f"exit {rc}") if rc != 0 else _verify_unit_output(Path(u["out"]), u["doc_ids"])
            rec = {"unit": u["unit"], "rc": rc, "ok": ok, "why": why, "s": round(time.monotonic() - t, 2),
                   "served_replies": dict(replies),
                   "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            if ok:
                rec["out_sha256"] = _sha(Path(u["out"]))
                summ = json.loads(Path(u["summary"]).read_text(encoding="utf-8"))
                rec.update(degraded=summ.get("degraded"), degraded_reasons=summ.get("degraded_reasons"),
                           llm_calls=summ.get("llm_calls"))
                done += 1
            else:
                failed += 1
            ledger.write(json.dumps(rec, sort_keys=True) + "\n")
            ledger.flush()
            os.fsync(ledger.fileno())
            if (done + failed) % 50 == 0:
                el = time.monotonic() - t0
                log.info("progress: %d done, %d failed of %d (%.0f s elapsed)", done, failed, len(todo), el)

    await asyncio.gather(*(one(u) for u in todo))
    ledger.close()
    log.info("finished: %d done, %d failed, served ids seen %s", done, failed, dict(check.seen))
    return 0 if failed == 0 and not stop_file.exists() else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--plan", required=True, help="plan.json written by alias_added_glossaries.py plan")
    p.add_argument("--units", type=int, default=4, help="units (so model calls) in flight; <= 4 for this endpoint")
    p.add_argument("--limit", type=int, default=0, help="run only the first N planned units (pilot)")
    p.add_argument("--only", default="", help="a file of unit ids to run (whitespace separated)")
    a = p.parse_args(argv)
    if not 1 <= a.units <= 4:
        raise SystemExit("--units must be 1..4 (the endpoint admits 4 sequences)")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return asyncio.run(run(a))


if __name__ == "__main__":
    sys.exit(main())
