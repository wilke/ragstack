"""Splice ``artifacts/stage0b-prime/TABLES.md`` into ``RESULTS-stage0b-prime.md``.

Every table in the write-up is a block of ``TABLES.md``, which ``s0b_report.py`` renders
from the committed JSON. The prose lives in ``RESULTS-stage0b-prime.md``; a line reading
``<!-- TABLE: <heading prefix> -->`` is replaced by that block, terminated by
``<!-- /TABLE -->``. Running this twice is a no-op: an already-spliced block is replaced by
the current rendering. No number in the write-up is transcribed by hand.
"""
from __future__ import annotations

import sys

import s0b_common as K
import s0_common as C  # noqa: F401

DOC = K.HERE / "RESULTS-stage0b-prime.md"
OPEN = "<!-- TABLE: "
CLOSE = "<!-- /TABLE -->"


def blocks() -> dict[str, str]:
    out: dict[str, str] = {}
    cur, buf = None, []
    for line in (K.ART / "TABLES.md").read_text().splitlines():
        if line.startswith("### "):
            if cur:
                out[cur] = "\n".join(buf).strip()
            cur, buf = line[4:].strip(), []
        else:
            buf.append(line)
    if cur:
        out[cur] = "\n".join(buf).strip()
    return out


def main() -> None:
    bl = blocks()
    lines = DOC.read_text().splitlines()
    out: list[str] = []
    i = 0
    n = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(OPEN) and line.rstrip().endswith("-->"):
            want = line[len(OPEN):].rstrip()[:-3].strip()
            hit = next((k for k in bl if k.startswith(want)), None)
            if hit is None:
                raise KeyError(f"no table block starting {want!r}; have {sorted(bl)}")
            out += [line, "", f"**{hit}**", "", bl[hit], "", CLOSE]
            n += 1
            i += 1
            # swallow a previously spliced body, if any
            j = i
            while j < len(lines) and not lines[j].startswith(OPEN):
                if lines[j].strip() == CLOSE:
                    i = j + 1
                    break
                j += 1
            continue
        out.append(line)
        i += 1
    DOC.write_text("\n".join(out) + "\n")
    print(f"spliced {n} tables")


if __name__ == "__main__":
    sys.exit(main())
