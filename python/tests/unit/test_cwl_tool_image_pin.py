"""Tree-wide tool-image pin (ADR-0010 decision 1, #655 step 1).

The tree is in exactly one of two states, and anything mixed fails:

* **unstamped** — every ``dockerPull`` in ``cwl/*.cwl`` is the bare
  ``ragstack-worker.sif`` (what ``main`` is today: dev runs ``main`` and
  resolves that name through its worker's ``--image-dir`` symlink);
* **stamped** — every ``dockerPull`` names one ``ragstack-tools-<version>-b<N>.sif``
  whose ``<version>`` equals this checkout's derived version, every
  ``dockerImageId`` is a 64-hex sha256, and the name ends in ``.sif`` (GoWe
  sends a name without the suffix to a registry).

Plus the stamping itself (``ragstack.tool_image.stamp_tool_image`` /
``scripts/stamp_tool_image.py``) on a copy of the real tree: a stamped copy
passes the check, a half-stamped one does not, and a receipt for another
version is refused.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ragstack import version as v
from ragstack.tool_image import (
    DEFAULT_TOOL_IMAGE,
    SHA256_RE,
    STAMPED_IMAGE_RE,
    ToolImageError,
    check_tree_state,
    image_sites,
    stamp_tool_image,
    stamped_image_name,
)

REPO = Path(__file__).resolve().parents[3]
CWL_DIR = REPO / "cwl"
STAMP = REPO / "python" / "scripts" / "stamp_tool_image.py"

DIGEST = "a" * 64


def _docs(cwl_dir: Path = CWL_DIR) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(cwl_dir.glob("*.cwl"))}


# --------------------------------------------------------------------------- #
# the tree as committed
# --------------------------------------------------------------------------- #


def test_tree_is_unstamped_or_stamped_never_mixed():
    state, name, problems = check_tree_state(_docs())
    assert not problems, "\n".join(problems)
    assert state in ("unstamped", "stamped"), state
    if state == "stamped":
        assert name is not None and name.endswith(".sif")
        m = STAMPED_IMAGE_RE.match(name)
        assert m, name
        derived = v.describe_repo(REPO).version  # dirty-tolerant: this is a check, not a build
        assert m.group("version") == derived, (
            f"stamped {name} but the checkout derives {derived}: re-stamp from a receipt "
            "built at this commit, or un-stamp to the bare name")
        for _, field, value in (s for d in _docs().values() for s in image_sites(d)):
            if field == "dockerImageId":
                assert SHA256_RE.match(value), value
    else:
        for _, field, value in (s for d in _docs().values() for s in image_sites(d)):
            assert value == DEFAULT_TOOL_IMAGE, (field, value)


def test_every_docker_pull_in_the_tree_ends_in_sif():
    """Load-bearing suffix: GoWe keeps a `.sif` name as a local image and
    prefixes anything else with docker:// (engine fact, #655)."""
    for source, text in _docs().items():
        for n, field, value in image_sites(text):
            if field == "dockerPull":
                assert value.endswith(".sif"), f"{source}:{n}: {value}"


def test_every_shipped_cwl_has_sites_in_rewritable_form_only():
    """A site the stamping cannot see would run an unstamped image on one step."""
    for source, text in _docs().items():
        stamp_tool_image(text, stamped_image_name("v9.9.9", 1), DIGEST, source=source)


def test_stamp_check_cli_agrees_with_this_test():
    out = subprocess.run(["python", str(STAMP), "--check", "--repo", str(REPO)],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith("ok: tree is ")


# --------------------------------------------------------------------------- #
# the stamping, on the real documents
# --------------------------------------------------------------------------- #


def test_stamping_the_tree_yields_a_stamped_tree():
    name = stamped_image_name("v9.9.9", 2)
    docs = {s: stamp_tool_image(t, name, DIGEST, source=s) for s, t in _docs().items()}
    state, got, problems = check_tree_state(docs)
    assert (state, got, problems) == ("stamped", name, [])
    before = sum(len(image_sites(t)) for t in _docs().values())
    after = [(f, val) for t in docs.values() for _, f, val in image_sites(t)]
    assert len(after) == before and before > 0
    assert all(val == name for f, val in after if f == "dockerPull")
    assert all(val == DIGEST for f, val in after if f == "dockerImageId")
    # Only the image lines changed.
    for s, t in _docs().items():
        changed = [(a, b) for a, b in zip(t.splitlines(), docs[s].splitlines(), strict=True)
                   if a != b]
        assert len(changed) == len(image_sites(t)), s
    # Re-stamping a stamped tree with another build rewrites it again.
    name3 = stamped_image_name("v9.9.9", 3)
    docs3 = {s: stamp_tool_image(t, name3, "b" * 64, source=s) for s, t in docs.items()}
    assert check_tree_state(docs3)[:2] == ("stamped", name3)


def test_half_stamped_tree_is_mixed():
    docs = _docs()
    with_sites = [s for s, t in docs.items() if image_sites(t)]
    assert len(with_sites) >= 2
    docs[with_sites[0]] = stamp_tool_image(docs[with_sites[0]], stamped_image_name("v9.9.9", 1),
                                           DIGEST)
    state, _, problems = check_tree_state(docs)
    assert state == "mixed" and problems


def test_stamp_refuses_a_bad_name_or_digest():
    doc = "a:\n  DockerRequirement:\n    dockerPull: ragstack-worker.sif\n    dockerImageId: ragstack-worker.sif\n"
    with pytest.raises(ToolImageError, match=r"\.sif"):
        stamp_tool_image(doc, "ragstack-tools-v1.0.0-b1", DIGEST)
    with pytest.raises(ToolImageError, match="stamped image name"):
        stamp_tool_image(doc, "ragstack-worker-v1.0.0.sif", DIGEST)
    with pytest.raises(ToolImageError, match="sha256"):
        stamp_tool_image(doc, stamped_image_name("v1.0.0", 1), "abc")
    with pytest.raises(ValueError):
        stamped_image_name("1.0.0", 1)  # no leading v: not a derived version
    with pytest.raises(ValueError):
        stamped_image_name("v1.0.0", 0)


@pytest.mark.parametrize("doc", [
    "a:\n  dockerPull: ragstack-worker.sif\nb: {dockerPull: ragstack-worker.sif}\n",
    "a:\n  dockerPull: ragstack-worker.sif\nb:\n  - dockerPull: ragstack-worker.sif\n",
    "a:\n  dockerPull: ragstack-worker.sif\nb:\n  dockerPull:\n    ragstack-worker.sif\n",
    "a:\n  dockerPull: ragstack-worker.sif\nb:\n  dockerPull : ragstack-worker.sif\n",
    "a:\n  dockerPull: ragstack-worker.sif\nb:\n  DockerPull: ragstack-worker.sif\n",
    "a:\n  dockerPull: ragstack-worker.sif\nb:\n  dockerPull: other\n",  # no .sif
], ids=["flow", "list", "next-line", "space-colon", "case", "no-sif"])
def test_stamp_refuses_on_partial(doc):
    with pytest.raises(ToolImageError):
        stamp_tool_image(doc, stamped_image_name("v1.0.0", 1), DIGEST)


def test_stamp_leaves_comments_and_prose_alone():
    doc = ("# dockerPull: ragstack-worker.sif in a comment\n"
           "doc: prose that says dockerPull is read by GoWe  # trailing\n"
           "    dockerPull: ragstack-worker.sif   # GoWe reads only this\n"
           "    dockerImageId: 'ragstack-worker.sif'\n")
    # The prose line names a docker key outside a comment and is not a site:
    # refused, because the checker cannot tell prose from a broken site.
    with pytest.raises(ToolImageError):
        stamp_tool_image(doc, stamped_image_name("v1.0.0", 1), DIGEST)
    doc2 = doc.replace("doc: prose that says dockerPull is read by GoWe  # trailing\n", "")
    out = stamp_tool_image(doc2, stamped_image_name("v1.0.0", 1), DIGEST)
    assert out.splitlines()[0] == doc2.splitlines()[0]
    assert "    dockerPull: ragstack-tools-v1.0.0-b1.sif   # GoWe reads only this" in out
    assert f"    dockerImageId: '{DIGEST}'" in out


# --------------------------------------------------------------------------- #
# the script, end to end, on a copy of the tree in a temp repo
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """cwl/ and pyproject.toml copied into a fresh repo tagged v9.9.9."""
    r = tmp_path / "r"
    (r / "python").mkdir(parents=True)
    shutil.copytree(CWL_DIR, r / "cwl")
    shutil.copy(REPO / "python" / "pyproject.toml", r / "python" / "pyproject.toml")
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.invalid")
    _git(r, "config", "user.name", "t")
    _git(r, "add", ".")
    _git(r, "commit", "-q", "-m", "copy")
    _git(r, "tag", "v9.9.9")
    return r


def _receipt(tmp_path: Path, version: str, build: int = 1, digest: str = DIGEST) -> Path:
    name = stamped_image_name(version, build)
    p = tmp_path / f"{name}.receipt.json"
    p.write_text(json.dumps({"name": name, "version": version, "commit": "c" * 40,
                             "build": str(build), "build_date": "2026-10-05T00:00:00Z",
                             "sha256": digest}))
    return p


def _run(tree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["python", str(STAMP), "--repo", str(tree), *args],
                          capture_output=True, text=True)


def test_script_stamps_then_check_passes(tree, tmp_path):
    out = _run(tree, str(_receipt(tmp_path, "v9.9.9")))
    assert out.returncode == 0, out.stderr + out.stdout
    state, name, problems = check_tree_state(_docs(tree / "cwl"))
    assert (state, name, problems) == ("stamped", "ragstack-tools-v9.9.9-b1.sif", [])
    assert re.search(r'^version = "9\.9\.9"$', (tree / "python/pyproject.toml").read_text(), re.M)
    # The stamp commits nothing itself; committed, the check holds at the tag…
    _git(tree, "commit", "-q", "-am", "stamp")
    _git(tree, "tag", "-f", "v9.9.9")
    assert _run(tree, "--check").returncode == 0
    # …and a receipt for a different build of the same version re-stamps.
    assert _run(tree, str(_receipt(tmp_path, "v9.9.9", 2, "b" * 64))).returncode == 0
    assert check_tree_state(_docs(tree / "cwl"))[1] == "ragstack-tools-v9.9.9-b2.sif"


def test_script_refuses_a_receipt_for_another_version(tree, tmp_path):
    before = _docs(tree / "cwl")
    out = _run(tree, str(_receipt(tmp_path, "v9.9.8")))
    assert out.returncode == 1 and "derives v9.9.9" in out.stderr
    # Nothing written — whatever state the copied tree was in (unstamped on
    # main today; stamped after the first stamped release), it still is.
    assert _docs(tree / "cwl") == before


def test_script_refuses_a_dirty_tree(tree, tmp_path):
    (tree / "cwl" / "pdf-ingest.cwl").write_text("# edited\n" + (tree / "cwl" / "pdf-ingest.cwl").read_text())
    out = _run(tree, str(_receipt(tmp_path, "v9.9.9")))
    assert out.returncode == 1 and "uncommitted" in out.stderr


def test_script_refuses_an_inconsistent_receipt(tree, tmp_path):
    p = tmp_path / "bad.receipt.json"
    p.write_text(json.dumps({"name": "ragstack-tools-v9.9.9-b2.sif", "version": "v9.9.9",
                             "commit": "c" * 40, "build": "1", "build_date": "x", "sha256": DIGEST}))
    out = _run(tree, str(p))
    assert out.returncode == 1 and "version + build" in out.stderr


def test_check_fails_a_mixed_tree(tree, tmp_path):
    assert _run(tree, str(_receipt(tmp_path, "v9.9.9"))).returncode == 0
    doc = tree / "cwl" / "pdf-ingest.cwl"
    doc.write_text(doc.read_text().replace("ragstack-tools-v9.9.9-b1.sif", DEFAULT_TOOL_IMAGE))
    out = _run(tree, "--check")
    assert out.returncode == 1 and "MIXED" in out.stderr


def test_check_fails_when_stamped_version_is_not_the_checkouts(tree, tmp_path):
    assert _run(tree, str(_receipt(tmp_path, "v9.9.9"))).returncode == 0
    _git(tree, "commit", "-q", "-am", "stamp")  # now v9.9.9+<sha>, stamped v9.9.9
    out = _run(tree, "--check")
    assert out.returncode == 1 and "checkout derives v9.9.9+" in out.stderr
