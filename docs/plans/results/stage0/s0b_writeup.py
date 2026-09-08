"""Splice ``artifacts/stage0b-prime/TABLES.md`` into ``RESULTS-stage0b-prime.md``.

Every table in the write-up is a block of ``TABLES.md``, which ``s0b_report.py`` renders
from the committed JSON. The prose lives in ``RESULTS-stage0b-prime.md`` between the
``<!-- TABLE: … -->`` markers; running this replaces each marker's block with the current
rendering, so no number in the write-up is transcribed by hand.
"""
from __future__ import annotations

import re
import sys

import s0b_common as K
import s0_common as C  # noqa: F401

DOC = K.HERE / "RESULTS-stage0b-prime.md"


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
    text = DOC.read_text()

    def repl(m):
        want = m.group(1).strip()
        for k, v in bl.items():
            if k.startswith(want):
                return f"<!-- TABLE: {want} -->\n\n**{k}**\n\n{v}\n\n<!-- /TABLE -->"
        raise KeyError(f"no table block starting {want!r}; have {sorted(bl)}")

    text = re.sub(r"<!-- TABLE: (.*?) -->.*?<!-- /TABLE -->", repl, text, flags=re.S)
    text = re.sub(r"<!-- TABLE: (.*?) -->(?!\n\n\*\*)", repl, text)
    DOC.write_text(text)
    print(f"spliced {len(re.findall(r'<!-- TABLE:', text))} tables")


if __name__ == "__main__":
    sys.exit(main())
