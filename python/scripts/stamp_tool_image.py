#!/usr/bin/env python
"""Stamp a built tools image into the tree (ADR-0010 decision 1, #655 step 1).

    python scripts/stamp_tool_image.py apptainer/images/ragstack-tools-v1.6.5-b1.sif.receipt.json
    python scripts/stamp_tool_image.py --check            # the release gate

Given the receipt ``apptainer/build-tools-image.sh`` wrote beside the image
(``{name, version, commit, build, build_date, sha256}``), rewrite every
``dockerPull`` in ``cwl/*.cwl`` to the receipt's ``name`` and every
``dockerImageId`` to its ``sha256``, and bump ``python/pyproject.toml``'s
static version to the receipt version's public part. A tagged checkout then
fixes tool + image by itself: GoWe's content hash changes whenever either does.

Refusals (non-zero exit, nothing written — every file is computed before any
is written):

* the receipt's ``version`` is not the checkout's derived version
  (``ragstack.version.derive_version`` — which also refuses a dirty tree):
  a receipt from another commit must not be stamped here;
* the receipt is not self-consistent (``name`` ≠
  ``ragstack-tools-<version>-b<N>.sif``, ``sha256`` not 64 hex);
* any ``dockerPull`` / ``dockerImageId`` site is in a form the rewrite cannot
  see (flow mapping, list item, ``dockerPull :``, key case, value on the
  next line) — the refuse-on-partial rule from #642, moved from boot time to
  release time;
* any current ``dockerPull`` does not end in ``.sif``. The suffix is
  load-bearing: GoWe keeps a ``.sif`` name as a local image and prefixes
  anything else with ``docker://`` (engine fact, 2026-10-04, on #655). A
  ``+`` in the name is safe — ``resolveApptainerImage`` is
  ``filepath.Join(imageDir, name)``, no escaping anywhere.

``--check`` runs the tree-wide gate without a receipt: the tree must be in
exactly one of two states — **unstamped** (every ``dockerPull`` is the bare
``ragstack-worker.sif``) or **stamped** (every ``dockerPull`` names one
``ragstack-tools-<version>-b<N>.sif`` whose ``<version>`` is the checkout's
derived version, every ``dockerImageId`` is a sha256, pyproject carries the
public version). Anything mixed fails. ``tests/unit/test_cwl_tool_image_pin.py``
is the same check as a unit test.

What ``dockerImageId`` buys, today: nothing at the engine. GoWe parses
``dockerImageId`` and never checks it (cwltool ``--singularity`` reads it as
a filename, so a digest there means cwltool cannot run a stamped CWL — the
GoWe plane is the one that matters). The digest is enforced only by our own
render/boot check (ADR-0010 decision 5, migration step 4); until that lands it
is a recorded fact a reader can verify with ``sha256sum``, not a gate.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "python"))

from ragstack.tool_image import (  # noqa: E402
    SHA256_RE,
    ToolImageError,
    check_tree_state,
    stamp_tool_image,
    stamped_image_name,
)
from ragstack.version import (  # noqa: E402
    NoTagError,
    VersionError,
    derive_version,
    describe_repo,
    pep440,
    split_version,
)

_PYPROJECT_VERSION_RE = re.compile(r'^(version\s*=\s*")([^"]*)(")\s*$', re.MULTILINE)


class StampError(RuntimeError):
    pass


def load_receipt(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise StampError(f"{path}: unreadable receipt: {e}") from e
    missing = [k for k in ("name", "version", "commit", "build", "build_date", "sha256")
               if not data.get(k)]
    if missing:
        raise StampError(f"{path}: receipt lacks {', '.join(missing)}")
    name, version, build, sha = (str(data[k]) for k in ("name", "version", "build", "sha256"))
    try:
        expected = stamped_image_name(version, int(build))
    except ValueError as e:
        raise StampError(f"{path}: {e}") from e
    if name != expected:
        raise StampError(f"{path}: name {name!r} is not {expected!r} (version + build)")
    if SHA256_RE.match(sha) is None:
        raise StampError(f"{path}: sha256 {sha!r} is not a 64-hex digest")
    if not re.fullmatch(r"[0-9a-f]{40}", str(data["commit"])):
        raise StampError(f"{path}: commit {data['commit']!r} is not a full sha")
    return {k: str(data[k]) for k in ("name", "version", "commit", "build", "build_date", "sha256")}


def cwl_docs(repo: Path) -> dict[Path, str]:
    return {p: p.read_text(encoding="utf-8") for p in sorted((repo / "cwl").glob("*.cwl"))}


def bump_pyproject(text: str, version: str) -> str:
    """Set ``[project] version`` to the public part of ``version`` (``v1.6.5+abc`` → ``1.6.5``)."""
    tag, _sha = split_version(version)
    public = pep440(tag)
    m = _PYPROJECT_VERSION_RE.search(text)
    if m is None:
        raise StampError("python/pyproject.toml has no `version = \"…\"` line")
    return text[: m.start()] + m.group(1) + public + m.group(3) + text[m.end():]


def check(repo: Path, *, receipt: dict[str, str] | None = None) -> list[str]:
    """The tree-wide gate. Returns the problems (empty = consistent)."""
    docs = {str(p.relative_to(repo)): t for p, t in cwl_docs(repo).items()}
    state, name, problems = check_tree_state(docs)
    try:
        derived = describe_repo(repo).version  # dirty-tolerant: a check, not a build
    except VersionError as e:
        return problems + [f"cannot derive the checkout's version: {e}"]
    tag, _sha = split_version(derived)
    pyproject = _PYPROJECT_VERSION_RE.search((repo / "python" / "pyproject.toml").read_text())
    static = pyproject.group(2) if pyproject else ""
    if static != pep440(tag):
        problems.append(f"python/pyproject.toml version {static!r} is not the public part "
                        f"{pep440(tag)!r} of the derived version {derived}")
    if state == "stamped" and name is not None:
        m = re.match(r"^ragstack-tools-(.+)-b[0-9]+\.sif$", name)
        stamped_version = m.group(1) if m else ""
        if stamped_version != derived:
            problems.append(f"stamped image {name} names version {stamped_version}, but the "
                            f"checkout derives {derived}")
        if receipt is not None and receipt["name"] != name:
            problems.append(f"tree is stamped with {name}, receipt names {receipt['name']}")
    elif state == "unstamped":
        if receipt is not None:
            problems.append("tree is unstamped; the receipt was not applied")
    elif state == "empty":
        problems.append("no dockerPull in cwl/*.cwl at all")
    else:
        problems.insert(0, "tree is MIXED: neither every dockerPull unstamped nor every one "
                           "stamped with one image")
    return problems


def stamp(repo: Path, receipt: dict[str, str]) -> list[Path]:
    """Compute every rewrite, then write. Returns the files that changed."""
    try:
        derived = derive_version(repo)
    except NoTagError as e:
        raise StampError(f"refused: {e}") from e
    except VersionError as e:
        raise StampError(f"refused: {e}") from e
    if receipt["version"] != derived:
        raise StampError(
            f"refused: receipt names version {receipt['version']} but this checkout derives "
            f"{derived}. Stamp from the commit the image was built from (receipt commit "
            f"{receipt['commit'][:12]})."
        )
    writes: dict[Path, str] = {}
    for path, text in cwl_docs(repo).items():
        try:
            new = stamp_tool_image(text, receipt["name"], receipt["sha256"],
                                   source=str(path.relative_to(repo)))
        except ToolImageError as e:
            raise StampError(f"refused: {e}") from e
        if new != text:
            writes[path] = new
    pyproject = repo / "python" / "pyproject.toml"
    bumped = bump_pyproject(pyproject.read_text(encoding="utf-8"), receipt["version"])
    if bumped != pyproject.read_text(encoding="utf-8"):
        writes[pyproject] = bumped
    for path, text in writes.items():
        path.write_text(text, encoding="utf-8")
    return sorted(writes)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("receipt", nargs="?", type=Path, help="<image>.receipt.json from build-tools-image.sh")
    ap.add_argument("--repo", type=Path, default=REPO, help="checkout to stamp (default: this one)")
    ap.add_argument("--check", action="store_true",
                    help="verify the tree is consistently unstamped or stamped; write nothing")
    args = ap.parse_args(argv)
    repo = args.repo.resolve()
    try:
        if args.check:
            problems = check(repo, receipt=load_receipt(args.receipt) if args.receipt else None)
            if problems:
                print("tool-image pin check FAILED:", file=sys.stderr)
                for p in problems:
                    print(f"  - {p}", file=sys.stderr)
                return 1
            state, name, _ = check_tree_state(
                {str(p.relative_to(repo)): t for p, t in cwl_docs(repo).items()})
            print(f"ok: tree is {state}" + (f" with {name}" if name else ""))
            return 0
        if args.receipt is None:
            ap.error("a receipt is required unless --check")
        receipt = load_receipt(args.receipt)
        changed = stamp(repo, receipt)
    except StampError as e:
        print(str(e), file=sys.stderr)
        return 1
    for p in changed:
        print(f"stamped {p.relative_to(repo)}")
    print(f"dockerPull → {receipt['name']}; dockerImageId → {receipt['sha256']}; "
          f"pyproject version → {pep440(split_version(receipt['version'])[0])}")
    problems = check(repo, receipt=receipt)
    if problems:
        print("post-stamp check FAILED (tree written):", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
