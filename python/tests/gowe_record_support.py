"""The FAILED submission record as the deployed GoWe engine (0.26.1) writes it.

Two facts shape it, and the classifiers in ``gowe_backend``, ``restore`` and
``graph.budget`` must be tested against both:

- ``error.context.exit_code`` is ``-1`` for EVERY failed apptainer tool; the
  tool's real code survives only in the worker's ``execute: apptainer command
  failed (exit N): `` prefix at the head of ``context.stderr``.
- the scheduler shortens a stderr longer than 1000 bytes (GoWe #338,
  ``stderrSnippet``): it keeps the FIRST line (capped at 200 bytes), then
  ``\\n...(truncated)...\\n``, then the END of the stderr, starting at a line
  boundary when one is near. The middle is what is lost.
"""
from __future__ import annotations

#: What GoWe's worker prepends to a failed apptainer tool's stderr.
EXEC_PREFIX = "execute: apptainer command failed (exit {code}): "
#: The scheduler's stderr budget and its cap on the kept first line.
STDERR_LIMIT = 1000
SNIPPET_HEAD = 200
SNIPPET_MARKER = "\n...(truncated)...\n"


def stderr_snippet(s: str, limit: int = STDERR_LIMIT) -> str:
    """GoWe's ``stderrSnippet`` for ASCII text: unchanged when it fits;
    otherwise the first line (<= 200 bytes), the marker, then the tail."""
    if len(s) <= limit:
        return s
    head = s.split("\n", 1)[0].rstrip("\r")[: min(SNIPPET_HEAD, limit // 2)]
    start = len(s) - (limit - len(head))
    tail = s[start:]
    if s[start - 1] != "\n":
        i = tail.find("\n")
        if 0 <= i < SNIPPET_HEAD and i + 1 < len(tail):
            tail = tail[i + 1:]
    return head + SNIPPET_MARKER + tail


def engine_stderr(code: int, tool_stderr: str) -> str:
    """``context.stderr`` for a tool that exited ``code`` printing ``tool_stderr``."""
    return stderr_snippet(EXEC_PREFIX.format(code=code) + tool_stderr)


def engine_record(code: int, tool_stderr: str, *, step: str = "pack",
                  sub_id: str = "sub_x") -> dict:
    """A FAILED submission exactly as the deployed engine records it."""
    return {"id": sub_id, "state": "FAILED", "error": {
        "code": "TASK_FAILED", "message": f"step '{step}' task failed with exit code -1",
        "context": {"step_id": step, "exit_code": -1,
                    "stderr": engine_stderr(code, tool_stderr)}}}
