#!/usr/bin/env python
"""Name a built tools image in the tree (ADR-0010, three-artifact model; Migration step 1 tooling).

    python scripts/stamp_tool_image.py /scout/containers/ragstack/ragstack-tools-v1.6.5-b1.sif.receipt.json
    python scripts/stamp_tool_image.py --check            # the tree-wide gate

Three artifacts, named separately (docs/adr-0010-three-artifacts):

1. a **tools image** — a build of the repo at tag ``T`` (labels
   ``org.ragstack.version=T``, ``org.ragstack.commit=sha(T)``), built FROM the
   tag commit by ``apptainer/build-tools-image.sh``; nothing is written back
   to the repo first;
2. a **workflow** — CWL text naming a tools image BY NAME; GoWe's
   content-hash id binds text + image name; "the tools image must support the
   workflow" is declared by the release that writes the name and proven by
   that release's tests;
3. a **server/tenant version** — a separate tag ``S`` that may differ from
   ``T``; a server release CHOOSES which tools image its CWL names.

Release order is linear (ADR-0010 decision 6): (a) tag ``T``; (b) build the
tools image; (c) ops places it in the shared store; (d) a server release
runs this script with the image's receipt, commits, tags ``S``. This script
is Migration step 1 tooling, used at release order (d): given the receipt ``build-tools-image.sh`` wrote beside
the image (``{name, version, commit, build, build_date, sha256}``), it writes
the receipt's ``name`` into every ``dockerPull`` **and** every
``dockerImageId`` of ``cwl/*.cwl`` (both keys carry the same bare filename;
GoWe reads the first, cwltool ``--singularity`` the second). It does NOT
touch ``pyproject.toml`` — that tracks the server release tag ``S``, not the
tools image — and it does NOT compare the receipt to the checkout's own
version: a dev server on ``main`` may name a ``vT+<sha>`` build.

Refusals (non-zero exit, nothing written — every file is computed before any
is written):

* the receipt is not self-consistent (``name`` ≠
  ``ragstack-tools-<version>-b<N>.sif``, ``sha256`` not 64 hex, ``commit``
  not a full sha);
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
``ragstack-worker.sif``) or **stamped** (every ``dockerPull`` names the SAME
well-formed ``ragstack-tools-<version>-b<N>.sif`` and every ``dockerImageId``
equals it). Anything mixed fails. ``tests/unit/test_cwl_tool_image_pin.py``
is the same check as a unit test.

Where the digest lives: in the receipt beside the image and in the image's
labels — not in the CWL. GoWe parses ``dockerImageId`` and never checks it;
cwltool reads it as the filename to look for; neither would verify a digest
there. Identity verification (ADR-0010 decision 7 / Migration step 4, the render/boot check) reads
the receipt and ``apptainer inspect --labels`` against the file the CWL
names; until that lands, nothing at the engine verifies the image beyond its
name.

**The committed receipt** (ADR-0010 Migration step 2, decision 8). Stamping also
writes the receipt it was given to ``cwl/tool-image.receipt.json`` — in git,
beside the ``dockerPull`` it belongs to — because that is where the API reads
``tool_image_digest`` from when it seeds a submission's provenance inputs: a
file's sha256 cannot live inside the file, so the worker cannot learn it, and
the shared store (Migration steps 3/4) is not something the API can read offline.
``--check`` holds the two together: a stamped tree must carry the receipt of
the image it names (same ``name``, a well-formed ``sha256``), an unstamped
tree must carry none. On an unstamped tree the API seeds no provenance inputs
at all (``ragstack.tool_image.provenance_inputs`` gates on a stamped name:
the deployed builds the bare name resolves to predate the tools' flags);
provenance begins with the first stamped release. A receipt without
``build_date`` (or any of the six keys) is refused, as is one whose ``name``
is not ``version`` + ``build``.
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
    RECEIPT_BASENAME,
    SHA256_RE,
    ToolImageError,
    check_tree_state,
    read_committed_receipt,
    stamp_tool_image,
    stamped_image_name,
)


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


def committed_receipt_path(repo: Path) -> Path:
    return repo / "cwl" / RECEIPT_BASENAME


def check(repo: Path, *, receipt: dict[str, str] | None = None) -> list[str]:
    """The tree-wide gate. Returns the problems (empty = consistent).

    Besides the CWL state, the committed receipt (``cwl/tool-image.receipt.json``,
    #655 step 2) must agree with it: a stamped tree carries the receipt of the
    image it names (the API reads the digest from it), an unstamped tree
    carries none (a stale receipt would lend its digest to an image the CWL
    does not name)."""
    docs = {str(p.relative_to(repo)): t for p, t in cwl_docs(repo).items()}
    state, name, problems = check_tree_state(docs)
    committed = read_committed_receipt(repo / "cwl")
    rel = committed_receipt_path(repo).relative_to(repo)
    if state == "stamped" and name is not None:
        if receipt is not None and receipt["name"] != name:
            problems.append(f"tree is stamped with {name}, receipt names {receipt['name']}")
        if committed is None:
            problems.append(f"tree is stamped with {name} but {rel} is missing: the API "
                            "records tool_image_digest from it (re-run the stamping with "
                            "the image's receipt)")
        else:
            cname = str(committed.get("name") or "")
            csha = str(committed.get("sha256") or "")
            if cname != name:
                problems.append(f"{rel} names {cname!r}, the tree is stamped with {name}")
            if SHA256_RE.match(csha) is None:
                problems.append(f"{rel}: sha256 {csha!r} is not a 64-hex digest")
            if receipt is not None and committed.get("sha256") != receipt["sha256"]:
                problems.append(f"{rel} sha256 differs from the given receipt's")
    elif state == "unstamped":
        if receipt is not None:
            problems.append("tree is unstamped; the receipt was not applied")
        if committed is not None:
            problems.append(f"tree is unstamped but {rel} exists (names "
                            f"{committed.get('name')!r}): a stale receipt would lend its "
                            "digest to an image the CWL does not name; remove it")
    elif state == "empty":
        problems.append("no dockerPull in cwl/*.cwl at all")
    else:
        problems.insert(0, "tree is MIXED: neither every dockerPull unstamped nor every one "
                           "stamped with one image")
    return problems


def stamp(repo: Path, receipt: dict[str, str]) -> list[Path]:
    """Compute every rewrite, then write. Returns the files that changed —
    the CWL documents and the committed receipt (``cwl/tool-image.receipt.json``,
    the receipt as given, keys sorted), which is written whenever its content
    would change."""
    writes: dict[Path, str] = {}
    for path, text in cwl_docs(repo).items():
        try:
            new = stamp_tool_image(text, receipt["name"], source=str(path.relative_to(repo)))
        except ToolImageError as e:
            raise StampError(f"refused: {e}") from e
        if new != text:
            writes[path] = new
    rpath = committed_receipt_path(repo)
    rtext = json.dumps(dict(receipt), indent=2, sort_keys=True) + "\n"
    try:
        current = rpath.read_text(encoding="utf-8")
    except OSError:
        current = None
    if current != rtext:
        writes[rpath] = rtext
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
    print(f"dockerPull and dockerImageId → {receipt['name']} "
          f"(version {receipt['version']}, build {receipt['build']}, sha256 {receipt['sha256'][:12]}… "
          "in the receipt, not the CWL)")
    problems = check(repo, receipt=receipt)
    if problems:
        print("post-stamp check FAILED (tree written):", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
