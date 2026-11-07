"""The checkpointed job runner shared by ``questions`` and ``retrieve``.

- At most ``in_flight`` jobs run at once (capped at 4, the gateway rule).
- Before a job STARTS, the pause file and the stop rules are checked; jobs already in
  flight always finish and their rows are written. So a pause "stops new calls after
  in-flight ones finish".
- Stop rules: more than ``max_failures`` failures in total, or ``max_consecutive``
  failures in a row. The total counts the failures already in the ledger (the whole run,
  not one segment) back to the last ``failure_reset`` event, which an operator writes
  with ``--reset-failures`` after looking at the cause. The run of consecutive failures
  restarts at 0 on every resume.
- Every finished job appends exactly one ledger row (fsync'd) from the event loop, so
  rows never interleave.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any

from .common import append_jsonl, iter_jsonl, log

FAILURE_RESET = "failure_reset"


def prior_failures(ledger: Path) -> int:
    n = 0
    for row in iter_jsonl(ledger):
        if row.get("event") == FAILURE_RESET:
            n = 0
        elif row.get("status") not in (None, "ok"):
            n += 1
    return n


def reset_failures(ledger: Path, note: str = "") -> None:
    append_jsonl(ledger, {"event": FAILURE_RESET, "t": round(time.time(), 2), "note": note})


class Control:
    def __init__(self, max_failures: int, max_consecutive: int, pause_file: Path | None,
                 prior_failures: int = 0) -> None:
        self.max_failures = int(max_failures)
        self.max_consecutive = int(max_consecutive)
        self.pause_file = pause_file
        self.fail_total = int(prior_failures)
        self.fail_consec = 0
        self.reason: str | None = None
        self.calls = 0
        self.failures_this_segment = 0

    def stop(self, reason: str) -> None:
        if self.reason is None:
            self.reason = reason
            log(f"stopping ({reason}); letting in-flight calls finish")

    def admit(self) -> bool:
        if self.reason is None and self.pause_file is not None and self.pause_file.exists():
            self.stop("pause_file")
        if self.reason is None and self.fail_total > self.max_failures:
            self.stop(f"failures_over_{self.max_failures}")
        return self.reason is None

    def ok(self) -> None:
        self.calls += 1
        self.fail_consec = 0

    def fail(self) -> None:
        self.calls += 1
        self.fail_total += 1
        self.fail_consec += 1
        self.failures_this_segment += 1
        if self.fail_consec >= self.max_consecutive:
            self.stop(f"failures_{self.max_consecutive}_in_a_row")
        elif self.fail_total > self.max_failures:
            self.stop(f"failures_over_{self.max_failures}")

    async def sleep(self, seconds: float) -> None:
        """Sleep (used for the empty-reply retry). A pause does not cut it short: the call
        is in flight and finishes."""
        await asyncio.sleep(seconds)


#: A job returns its ledger row; ``row["status"] == "ok"`` marks success.
Job = Callable[[], Awaitable[dict[str, Any]]]


async def run_pool(jobs: Iterable[Job], control: Control, in_flight: int,
                   ledger: Path | Callable[[dict[str, Any]], Path]) -> int:
    """Run ``jobs`` with at most ``in_flight`` concurrently; return the number started.
    ``ledger`` is a path, or a function of the finished row naming its ledger (used by
    ``retrieve`` to interleave arms while keeping one ledger per arm)."""
    ledger_of = ledger if callable(ledger) else (lambda _row: ledger)
    it = iter(jobs)
    started = 0
    running: set[asyncio.Task] = set()

    def next_job() -> Job | None:
        if not control.admit():
            return None
        return next(it, None)

    while True:
        while len(running) < in_flight:
            job = next_job()
            if job is None:
                break
            running.add(asyncio.ensure_future(job()))
            started += 1
        if not running:
            break
        done, running = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            try:
                row = t.result()
            except Exception as e:  # noqa: BLE001 - a job bug must not lose the ledger
                row = {"status": f"error:{type(e).__name__}"}
            append_jsonl(ledger_of(row), {**row, "t": round(time.time(), 2)})
            if row.get("status") == "ok":
                control.ok()
            else:
                control.fail()
    return started


def chat_url(url: str) -> str:
    u = url.rstrip("/")
    if u.endswith("/chat/completions"):
        return u
    if u.endswith("/v1"):
        return u + "/chat/completions"
    return u + "/v1/chat/completions"
