#!/usr/bin/env python3
"""Verify that every numeric claim a paper makes still matches its committed artifact.

WHAT THIS IS
------------
A paper in `docs/papers/<paper>/` ships a `claims.json` beside it: a machine-readable
inventory of every number the prose asserts, each one pinned to the committed artifact
that holds it. This script resolves every claim against the repository and reports
PASS/FAIL. It reads; it never writes, never runs an experiment and never contacts an
endpoint or a store.

Run it before submitting, and in CI, so that a re-run which moves a number cannot
silently leave a stale figure in the prose.

    /rag/envs/ragstack/bin/python3 docs/papers/check_claims.py            # every claims.json
    /rag/envs/ragstack/bin/python3 docs/papers/check_claims.py PATH ...   # specific ones
    /rag/envs/ragstack/bin/python3 docs/papers/check_claims.py --json     # machine output

Exit status is 0 only if every claim verified.

HOW A PAPER CITES A CLAIM
-------------------------
Each claim has a stable kebab-case `id`. The prose cites it as a marker the build (or a
reviewer) can grep for, e.g.

    ... reaches a Spearman-Brown reliability of 0.92 at thirty pooled readings
    [claim:graded-support-reliability-at-30] ...

The `id` is the contract. `value`, `source` and `pointer` may be re-measured and
updated; the id stays, so the citation in the prose never goes stale, and
`grep -o 'claim:[a-z0-9-]*'` over the manuscript cross-checked against the ids in
`claims.json` tells you which claims are cited and which asserted numbers are not
covered by the inventory.

A claim with `"verified": false` is one the author could not resolve; its `note` says
why. Those are reported as SKIP-FALSE and do not fail the run, but they must not appear
in the prose as if they were checked.

CLAIM SCHEMA
------------
    id        short kebab-case identifier, unique within the file
    section   which step of the paper's argument it supports
    statement the sentence the paper would assert, with the number in it
    value     the number (JSON number), or an object / array for multi-part claims
    source    repo-relative path to the committed artifact
    pointer   how to find it in that artifact (see below)
    kind      "json" or "text"
    verified  whether the author resolved it
    note      what a reader must know so as not to misread it

POINTER SYNTAX
--------------
kind "json": a dotted path into the parsed document. A segment containing a dot, a
space or a quote goes in brackets: `within_family["opus5 vs fable51"].whether_kappa.kappa`.
A numeric segment indexes a list: `pooled.support_reliability[6].spearman_brown_full_n`.
An empty pointer, or `$`, is the document root — useful for a multi-part claim whose
parts live at top level. A multi-part key may itself be bracket-quoted when the key it
names contains a dot, e.g. `["frac_pairs_r_at_least_0.5"]`.

  - a scalar `value` is compared to whatever the pointer resolves to;
  - an object `value` is a multi-part claim: each key is itself a pointer *relative to*
    `pointer` (so it may be dotted), and each is resolved and compared;
  - a list `value` is compared element-wise to the resolved list.

kind "text": `pointer` is an exact substring that must occur in the file exactly once,
and the rendered `value` must itself be a substring of `pointer`. Markdown is the
fallback source, used only where a number exists in no committed JSON.

COMPARISON
----------
Floats compare within a relative tolerance of 1e-9 (exact for 0). Booleans and None
compare by identity, never by Python's int/bool equivalence. Strings compare exactly.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REL_TOL = 1e-9

# docs/papers/check_claims.py -> docs/papers -> docs -> <repo root>
PAPERS_DIR = Path(__file__).resolve().parent
REPO_ROOT = PAPERS_DIR.parent.parent


class ResolveError(Exception):
    pass


def parse_pointer(pointer: str) -> list[str]:
    """Split a pointer into path segments, honouring ["..."] bracket quoting."""
    segments: list[str] = []
    buf = ""
    i = 0
    n = len(pointer)
    while i < n:
        ch = pointer[i]
        if ch == "[":
            if buf:
                segments.append(buf)
                buf = ""
            close = pointer.find("]", i)
            if close == -1:
                raise ResolveError(f"unterminated '[' in pointer {pointer!r}")
            seg = pointer[i + 1 : close]
            if len(seg) >= 2 and seg[0] in "\"'" and seg[-1] == seg[0]:
                seg = seg[1:-1]
            segments.append(seg)
            i = close + 1
            if i < n and pointer[i] == ".":
                i += 1
        elif ch == ".":
            if buf:
                segments.append(buf)
                buf = ""
            i += 1
        else:
            buf += ch
            i += 1
    if buf:
        segments.append(buf)
    return segments


def resolve(doc, pointer: str):
    if pointer in ("", "$"):
        return doc
    node = doc
    walked: list[str] = []
    for seg in parse_pointer(pointer):
        walked.append(seg)
        here = ".".join(walked)
        if isinstance(node, dict):
            if seg not in node:
                raise ResolveError(f"no key {seg!r} at {here!r}")
            node = node[seg]
        elif isinstance(node, list):
            try:
                idx = int(seg)
            except ValueError:
                raise ResolveError(f"list needs an integer index, got {seg!r} at {here!r}") from None
            if not -len(node) <= idx < len(node):
                raise ResolveError(f"index {idx} out of range (len {len(node)}) at {here!r}")
            node = node[idx]
        else:
            raise ResolveError(f"cannot descend into {type(node).__name__} at {here!r}")
    return node


def scalars_equal(expected, actual) -> tuple[bool, str]:
    if expected is None or actual is None:
        ok = expected is None and actual is None
        return ok, "" if ok else f"expected {expected!r}, found {actual!r}"
    if isinstance(expected, bool) or isinstance(actual, bool):
        ok = isinstance(expected, bool) and isinstance(actual, bool) and expected is actual
        return ok, "" if ok else f"expected {expected!r}, found {actual!r}"
    if isinstance(expected, str) or isinstance(actual, str):
        ok = expected == actual
        return ok, "" if ok else f"expected {expected!r}, found {actual!r}"
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if expected == 0 or actual == 0:
            ok = float(expected) == float(actual)
        else:
            ok = math.isclose(float(expected), float(actual), rel_tol=REL_TOL, abs_tol=0.0)
        return ok, "" if ok else f"expected {expected!r}, found {actual!r}"
    return False, f"uncomparable types: expected {type(expected).__name__}, found {type(actual).__name__}"


def renderings(value) -> list[str]:
    out = [str(value)]
    if isinstance(value, bool):
        return out
    if isinstance(value, int):
        out.append(f"{value:,}")
    elif isinstance(value, float):
        out.append(f"{value:,}")
        if value == int(value):
            out.append(str(int(value)))
            out.append(f"{int(value):,}")
    seen: list[str] = []
    for r in out:
        if r not in seen:
            seen.append(r)
    return seen


def check_json_claim(doc, claim) -> list[str]:
    pointer = claim["pointer"]
    value = claim["value"]
    problems: list[str] = []
    if isinstance(value, dict):
        for sub, sub_expected in value.items():
            if pointer in ("", "$"):
                sub_pointer = sub
            elif sub.startswith("["):
                sub_pointer = pointer + sub
            else:
                sub_pointer = f"{pointer}.{sub}"
            try:
                actual = resolve(doc, sub_pointer)
            except ResolveError as exc:
                problems.append(f"{sub}: {exc}")
                continue
            ok, why = scalars_equal(sub_expected, actual)
            if not ok:
                problems.append(f"{sub}: {why}")
        return problems
    try:
        actual = resolve(doc, pointer)
    except ResolveError as exc:
        return [str(exc)]
    if isinstance(value, list):
        if not isinstance(actual, list):
            return [f"expected a list, found {type(actual).__name__}"]
        if len(value) != len(actual):
            return [f"expected {len(value)} elements, found {len(actual)}"]
        for i, (exp, act) in enumerate(zip(value, actual)):
            ok, why = scalars_equal(exp, act)
            if not ok:
                problems.append(f"[{i}]: {why}")
        return problems
    ok, why = scalars_equal(value, actual)
    return [] if ok else [why]


def check_text_claim(text: str, claim) -> list[str]:
    pointer = claim["pointer"]
    value = claim["value"]
    occurrences = text.count(pointer)
    if occurrences == 0:
        return ["pointer substring not found in the file"]
    if occurrences > 1:
        return [f"pointer substring is not unique ({occurrences} occurrences)"]
    if isinstance(value, (dict, list)):
        return ["a text claim's value must be a scalar"]
    if not any(r in pointer for r in renderings(value)):
        return [f"value {value!r} does not appear inside the pointer substring"]
    return []


def check_file(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    claims = data.get("claims", [])
    doc_cache: dict[Path, object] = {}
    text_cache: dict[Path, str] = {}
    results = []
    seen_ids: set[str] = set()

    for claim in claims:
        cid = claim.get("id", "<no id>")
        status = "PASS"
        problems: list[str] = []

        if cid in seen_ids:
            problems.append("duplicate claim id")
        seen_ids.add(cid)

        source = REPO_ROOT / claim["source"]
        if not source.is_file():
            problems.append(f"source not found: {claim['source']}")
        elif claim.get("kind") == "json":
            try:
                if source not in doc_cache:
                    doc_cache[source] = json.loads(source.read_text(encoding="utf-8"))
                problems += check_json_claim(doc_cache[source], claim)
            except json.JSONDecodeError as exc:
                problems.append(f"source is not valid JSON: {exc}")
        elif claim.get("kind") == "text":
            if source not in text_cache:
                text_cache[source] = source.read_text(encoding="utf-8")
            problems += check_text_claim(text_cache[source], claim)
        else:
            problems.append(f"unknown kind {claim.get('kind')!r}")

        if not claim.get("verified", False):
            status = "SKIP-FALSE"
        elif problems:
            status = "FAIL"

        results.append(
            {
                "id": cid,
                "section": claim.get("section"),
                "status": status,
                "source": claim.get("source"),
                "pointer": claim.get("pointer"),
                "problems": problems,
            }
        )

    return {
        "claims_file": str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path),
        "paper": data.get("paper"),
        "generated_utc": data.get("generated_utc"),
        "n_claims": len(claims),
        "results": results,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*", help="claims.json files (default: all under docs/papers/)")
    ap.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")
    args = ap.parse_args(argv)

    if args.paths:
        paths = [Path(p).resolve() for p in args.paths]
    else:
        paths = sorted(PAPERS_DIR.rglob("claims.json"))

    if not paths:
        print("no claims.json found under docs/papers/", file=sys.stderr)
        return 2

    reports = []
    for path in paths:
        if not path.is_file():
            print(f"not a file: {path}", file=sys.stderr)
            return 2
        reports.append(check_file(path))

    n_pass = sum(1 for r in reports for c in r["results"] if c["status"] == "PASS")
    n_fail = sum(1 for r in reports for c in r["results"] if c["status"] == "FAIL")
    n_skip = sum(1 for r in reports for c in r["results"] if c["status"] == "SKIP-FALSE")
    total = n_pass + n_fail + n_skip

    if args.as_json:
        print(
            json.dumps(
                {
                    "summary": {"total": total, "pass": n_pass, "fail": n_fail, "unverified": n_skip},
                    "files": reports,
                },
                indent=2,
            )
        )
    else:
        for report in reports:
            print(f"== {report['claims_file']}  ({report['n_claims']} claims)")
            for c in report["results"]:
                line = f"  {c['status']:<10} [{c['section']}] {c['id']}"
                print(line)
                for p in c["problems"]:
                    print(f"             ! {p}")
            print()
        print(f"{total} claims: {n_pass} PASS, {n_fail} FAIL, {n_skip} marked unverified")

    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
