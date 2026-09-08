"""Splice ``artifacts/pointed-scale/TABLES.md`` into ``RESULTS-pointed-at-scale.md``.

Same contract as ``s0b_writeup.py``: the prose lives in the write-up, every table is a
``### <title>`` block of ``TABLES.md`` rendered by ``s0s_report.py`` from the committed
JSON, and a line reading ``<!-- TABLE: <title prefix> -->`` is replaced by that block,
terminated by ``<!-- /TABLE -->``. Running it twice is a no-op. No number in the write-up
is transcribed by hand.
"""
from __future__ import annotations

import sys

import s0s_common as SC

DOC = SC.HERE / "RESULTS-pointed-at-scale.md"
OPEN = "<!-- TABLE: "
CLOSE = "<!-- /TABLE -->"


def blocks() -> dict[str, str]:
    out: dict[str, str] = {}
    cur, buf = None, []
    for line in (SC.ART / "TABLES.md").read_text().splitlines():
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
    i = n = 0
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
