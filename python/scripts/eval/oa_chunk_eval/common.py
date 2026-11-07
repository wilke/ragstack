"""Shared I/O and text helpers: JSONL with fsync'd appends, hashing, whitespace
normalisation with an index map, corpus paths."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

_WS = re.compile(r"\s+")


def log(msg: str) -> None:
    print(f"[oa_chunk_eval {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def seeded_rank(seed: int | str, *parts: str) -> str:
    """A stable sort key: sha256 of seed + parts. Independent of Python's ``random``
    implementation and of the order items are seen in."""
    return sha256_text("\x1f".join([str(seed), *parts]))


def seeded_unit(seed: int | str, *parts: str) -> float:
    """A stable pseudo-uniform number in [0, 1) from seed + parts."""
    return int(seeded_rank(seed, *parts)[:13], 16) / float(16 ** 13)


def corpus_rel_path(pmcid: str) -> str:
    """``clean/xx/yy/PMC….xml`` with xx/yy = sha1(pmcid)[:2] / [2:4] (HARNESS-SPEC, config)."""
    h = hashlib.sha1(pmcid.encode("utf-8")).hexdigest()
    return f"clean/{h[:2]}/{h[2:4]}/{pmcid}.xml"


def article_of(source_path: str) -> str:
    """Record path -> article id: ``PMC123#table-2`` -> ``PMC123``."""
    return (source_path or "").split("#", 1)[0]


def norm_ws(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


def norm_ws_map(text: str) -> tuple[str, list[int]]:
    """Whitespace-normalised ``text`` plus, per output char, its index in ``text``.

    Runs of whitespace collapse to one space; leading/trailing whitespace is dropped.
    ``idx[i]`` is the source offset of output char ``i``, so an output span
    ``[a, b)`` maps back to ``[idx[a], idx[b-1] + 1)``.
    """
    out: list[str] = []
    idx: list[int] = []
    pending_space = -1
    for i, ch in enumerate(text):
        if ch.isspace():
            if out and pending_space < 0:
                pending_space = i
            continue
        if pending_space >= 0:
            out.append(" ")
            idx.append(pending_space)
            pending_space = -1
        out.append(ch)
        idx.append(i)
    return "".join(out), idx


# ------------------------------------------------------------------- JSONL


def iter_jsonl(path: str | Path) -> Iterator[dict[str, Any]]:
    """Rows of a JSONL file. A truncated final line (a crash mid-append) is skipped;
    any other malformed line raises, since that is not a crash artefact."""
    p = Path(path)
    if not p.exists():
        return
    with p.open(encoding="utf-8") as fh:
        lines = fh.readlines()
    for n, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        try:
            row = json.loads(s)
        except json.JSONDecodeError:
            if n == len(lines) - 1 and not line.endswith("\n"):
                log(f"{p.name}: ignoring a truncated last line")
                continue
            raise
        yield row


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    return list(iter_jsonl(path))


def iter_jsonl_stream(path: str | Path) -> Iterator[dict[str, Any]]:
    """Line-streaming reader for large inputs (the extracted corpus). Bad lines are skipped."""
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s:
                continue
            try:
                yield json.loads(s)
            except json.JSONDecodeError:
                continue


def repair_tail(path: str | Path) -> str | None:
    """Make sure a JSONL file ends with a newline before anything is appended.

    A crash mid-append can leave a partial last line with no ``\\n``. Appending straight
    after it would glue the next row onto it, and the file would stop being readable
    (the bad line is no longer last). If the tail parses as JSON (only the newline was
    lost) a ``\\n`` is added; otherwise the partial line is truncated away. Returns
    ``"newline"``, ``"truncated"`` or None (nothing to do)."""
    p = Path(path)
    if not p.exists():
        return None
    with p.open("rb+") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        if size == 0:
            return None
        fh.seek(size - 1)
        if fh.read(1) == b"\n":
            return None
        # find the start of the last line, reading backwards in blocks
        pos, block, start = size, 65536, 0
        while pos > 0:
            step = min(block, pos)
            pos -= step
            fh.seek(pos)
            buf = fh.read(step)
            nl = buf.rfind(b"\n")
            if nl >= 0:
                start = pos + nl + 1
                break
        fh.seek(start)
        tail = fh.read()
        try:
            json.loads(tail.decode("utf-8"))
            fh.seek(size)
            fh.write(b"\n")
            action = "newline"
        except (UnicodeDecodeError, json.JSONDecodeError):
            fh.truncate(start)
            action = "truncated"
        fh.flush()
        os.fsync(fh.fileno())
    log(f"{p.name}: repaired the last line before appending ({action})")
    return action


def append_jsonl(path: str | Path, row: dict[str, Any]) -> None:
    """Append one row and fsync, so a checkpoint survives a crash. A partial last line
    left by an earlier crash is repaired first (:func:`repair_tail`)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    repair_tail(p)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> int:
    """Atomic rewrite (tmp + rename). Returns the row count."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    n = 0
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    tmp.replace(p)
    return n


def write_json(path: str | Path, obj: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(p)


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))
