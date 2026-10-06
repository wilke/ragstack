"""``apptainer/build-tools-image.sh`` — the logic, without a real build.

Each case runs the script with ``--repo`` pointing at a temporary git
repository that carries just enough of the tree (``python/ragstack/version.py``
and the def file) for the version step, in ``--dry-run`` so nothing is built:

* name/receipt derivation — ``ragstack-tools-<version>-b<N>.sif`` with
  ``<version>`` the derived version and ``N`` the next free build number in
  ``--out``;
* the dirty-tree refusal (exit 3, the message from ``ragstack.version``);
* the exact ``apptainer build`` command, with the five ``--build-arg``s, and
  the staging step (``git archive HEAD python``) that makes the image ship
  the COMMIT's ``python/`` rather than the working tree's.

A real build (fakeroot, network, ~15 s) is opt-in: set
``RAGSTACK_BUILD_TESTS=1`` and the last test builds a *tiny* def through the
whole script into a temp dir — labels verified, ``/opt/ragstack/RELEASE``
compared, sha256 computed, receipt written — and proves that a gitignored
file under ``python/`` is NOT in the image while the committed one is.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "apptainer" / "build-tools-image.sh"
DEF = REPO / "apptainer" / "ragstack-tools.def"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    (r / "python" / "ragstack").mkdir(parents=True)
    (r / "apptainer").mkdir()
    for f in ("__init__.py", "version.py"):
        shutil.copy(REPO / "python" / "ragstack" / f, r / "python" / "ragstack" / f)
    shutil.copy(DEF, r / "apptainer" / "ragstack-tools.def")
    (r / ".gitignore").write_text("__pycache__/\n*.env\n")  # as the real tree ignores caches/env
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.invalid")
    _git(r, "config", "user.name", "t")
    _git(r, "add", ".")
    _git(r, "commit", "-q", "-m", "one")
    _git(r, "tag", "v3.2.1")
    return r


def _run(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    # PYTHON pins the interpreter to the test's own (>= 3.11): the script's
    # default-interpreter rule has its own tests below, which drop it.
    e = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"),
         "PYTHON": sys.executable}
    if env:
        e.update(env)
    e = {k: v for k, v in e.items() if v is not None}
    return subprocess.run(["bash", str(SCRIPT), "--repo", str(repo), *args],
                          capture_output=True, text=True, env=e)


def test_dry_run_names_the_build_and_prints_the_command(repo, tmp_path):
    out = _run(repo, "--dry-run", "--out", str(tmp_path / "images"))
    assert out.returncode == 0, out.stderr
    commit = _git(repo, "rev-parse", "HEAD")
    assert "version:    v3.2.1\n" in out.stdout
    assert f"commit:     {commit}\n" in out.stdout
    assert "build:      1\n" in out.stdout
    assert f"image:      {tmp_path / 'images'}/ragstack-tools-v3.2.1-b1.sif\n" in out.stdout
    cmd = re.search(r"^command:    (.*)$", out.stdout, re.M).group(1)
    assert cmd.startswith("apptainer build --fakeroot --build-arg VERSION=v3.2.1 --build-arg COMMIT=" + commit)
    assert "--build-arg BUILD=1 --build-arg BUILD_DATE=" in cmd
    assert re.search(r"--build-arg SRC=\S+/ragstack-tools-src\.\d+/python ", cmd)
    assert cmd.rstrip().endswith("ragstack-tools-v3.2.1-b1.sif " + str(repo / "apptainer/ragstack-tools.def"))
    receipt = json.loads(out.stdout.split("---\n", 1)[1])
    assert receipt == {"name": "ragstack-tools-v3.2.1-b1.sif", "version": "v3.2.1", "commit": commit,
                       "build": "1", "build_date": receipt["build_date"], "sha256": None}
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", receipt["build_date"])
    assert not (tmp_path / "images").exists(), "dry run must create nothing"


def test_the_source_is_staged_from_the_commit_not_the_working_tree(repo, tmp_path):
    """`git describe --dirty` and any untracked-file check ignore gitignored
    content, and %files would copy python/ wholesale (a build from the tree
    shipped .mypy_cache, __pycache__ and would ship a python/.env). The script
    therefore stages python/ with `git archive HEAD` and hands the def that
    path as SRC; the def's %files reads it from there."""
    (repo / "python" / "secrets.env").write_text("gitignored")  # ignored, present in the tree
    out = _run(repo, "--dry-run", "--out", str(tmp_path / "images"))
    assert out.returncode == 0, out.stderr  # not dirty: ignored files are not changes
    stage = re.search(r"^stage:      (.*)$", out.stdout, re.M).group(1)
    assert stage.startswith(f"git -C {repo} archive --format=tar HEAD python | tar -x -C ")
    assert "{{ SRC }} /opt/ragstack/python" in DEF.read_text()
    assert not re.search(r"^\s*python /opt/ragstack/python", DEF.read_text(), re.M)


def test_build_number_is_the_next_free_one_for_that_version(repo, tmp_path):
    out_dir = tmp_path / "images"
    out_dir.mkdir()
    (out_dir / "ragstack-tools-v3.2.1-b1.sif").write_bytes(b"")
    (out_dir / "ragstack-tools-v3.2.1-b3.sif").write_bytes(b"")
    (out_dir / "ragstack-tools-v9.9.9-b7.sif").write_bytes(b"")  # another version: ignored
    out = _run(repo, "--dry-run", "--out", str(out_dir))
    assert out.returncode == 0, out.stderr
    assert "build:      4\n" in out.stdout  # max+1, never a gap re-used


def test_build_number_counts_the_release_store_too(repo, tmp_path):
    """#673 F4: a fresh worktree's --out is empty; --out alone minted b1 while
    the store already held ragstack-tools-<version>-b1.sif. N is now 1 + the
    max across --out AND every --store (repeatable or comma-separated; a
    receipt without its .sif counts), and the store is read-only."""
    out_dir = tmp_path / "images"
    s1, s2, s3 = tmp_path / "store1", tmp_path / "store2", tmp_path / "store3"
    for d in (s1, s2, s3):
        d.mkdir()
    (s1 / "ragstack-tools-v3.2.1-b1.sif").write_bytes(b"")
    (s1 / "ragstack-tools-v9.9.9-b8.sif").write_bytes(b"")  # another version: ignored
    out = _run(repo, "--dry-run", "--out", str(out_dir), "--store", str(s1))
    assert out.returncode == 0, out.stderr
    assert "build:      2\n" in out.stdout and "ragstack-tools-v3.2.1-b2.sif" in out.stdout
    (s2 / "ragstack-tools-v3.2.1-b2.sif").write_bytes(b"")
    (s3 / "ragstack-tools-v3.2.1-b4.sif.receipt.json").write_text("{}")  # receipt only
    out = _run(repo, "--dry-run", "--out", str(out_dir), "--store", f"{s1},{s2}", "--store", str(s3))
    assert out.returncode == 0, out.stderr
    assert "build:      5\n" in out.stdout
    assert f"stores:     {s1} {s2} {s3}\n" in out.stdout
    # Without --store: the collision the finding describes (b1 again).
    out = _run(repo, "--dry-run", "--out", str(out_dir))
    assert "build:      1\n" in out.stdout
    assert sorted(p.name for p in s1.iterdir()) == ["ragstack-tools-v3.2.1-b1.sif",
                                                    "ragstack-tools-v9.9.9-b8.sif"]


def test_a_missing_store_dir_is_refused(repo, tmp_path):
    """A typo'd --store would silently restart the numbering at b1."""
    out = _run(repo, "--dry-run", "--out", str(tmp_path / "images"),
               "--store", str(tmp_path / "no-such-store"))
    assert out.returncode == 2
    assert "--store" in out.stderr and "not a directory" in out.stderr


def test_build_names_never_reuse_a_store_entry_of_any_kind(repo, tmp_path):
    """The name the script settles on must not exist in --out or any --store.
    The explicit `-e` refusal after the scan is belt and braces (it guards a
    concurrent build; max+1 cannot hit a well-formed name the scan saw), so
    this holds the scan to entries that are not plain files: a dangling
    symlink and a directory named like a build both count."""
    store = tmp_path / "store"
    store.mkdir()
    (store / "ragstack-tools-v3.2.1-b1.sif").write_bytes(b"")
    (store / "ragstack-tools-v3.2.1-b2.sif").symlink_to(tmp_path / "gone")  # dangling
    (store / "ragstack-tools-v3.2.1-b3.sif.receipt.json").mkdir()
    out = _run(repo, "--dry-run", "--out", str(tmp_path / "images"), "--store", str(store))
    assert out.returncode == 0, out.stdout + out.stderr
    assert "build:      4\n" in out.stdout
    assert 'refusing: $dir/$NAME (or its receipt) already exists' in SCRIPT.read_text()


def _stub_python3(tmp_path: Path, ok: bool) -> Path:
    """A PATH dir whose `python3` passes or fails the >= 3.11 probe and
    otherwise defers to the real interpreter."""
    d = tmp_path / ("py-ok" if ok else "py-old")
    d.mkdir()
    stub = d / "python3"
    if ok:
        stub.write_text(f'#!/bin/sh\nexec {sys.executable} "$@"\n')
    else:
        stub.write_text("#!/bin/sh\n"
                        'if [ "$1" = "--version" ]; then echo "Python 3.8.5"; exit 0; fi\n'
                        "exit 1\n")
    stub.chmod(0o755)
    return d


def test_default_interpreter_is_python3_only_when_it_is_3_11_plus(repo, tmp_path):
    """#673 F5: the default was `python`, which on coconut is miniconda 3.8 and
    dies on datetime.UTC. Now: $PYTHON, else python3 if >= 3.11, else a
    refusal naming --python and PYTHON."""
    path = os.environ["PATH"]
    old = _stub_python3(tmp_path, ok=False)
    out = _run(repo, "--dry-run", "--out", str(tmp_path / "images"),
               env={"PYTHON": None, "PATH": f"{old}:{path}"})
    assert out.returncode == 2, out.stdout + out.stderr
    assert "--python" in out.stderr and "PYTHON" in out.stderr and "3.11" in out.stderr
    assert "Python 3.8.5" in out.stderr

    good = _stub_python3(tmp_path, ok=True)
    out = _run(repo, "--dry-run", "--out", str(tmp_path / "images"),
               env={"PYTHON": None, "PATH": f"{good}:{path}"})
    assert out.returncode == 0, out.stderr
    assert "build:      1\n" in out.stdout

    # $PYTHON wins over a too-old python3; --python wins over $PYTHON.
    out = _run(repo, "--dry-run", "--out", str(tmp_path / "images"),
               env={"PYTHON": sys.executable, "PATH": f"{old}:{path}"})
    assert out.returncode == 0, out.stderr
    out = _run(repo, "--dry-run", "--out", str(tmp_path / "images"), "--python", sys.executable,
               env={"PYTHON": str(tmp_path / "nope"), "PATH": f"{old}:{path}"})
    assert out.returncode == 0, out.stderr


def test_usage_documents_every_flag(repo):
    out = _run(repo, "--help")
    assert out.returncode == 0
    for flag in ("--out", "--store", "--repo", "--python", "--dry-run", "--sandbox"):
        assert flag in out.stdout, flag
    assert "set -euo" not in out.stdout


def test_off_tag_version_carries_the_sha_and_a_plus_in_the_name(repo, tmp_path):
    (repo / "python" / "x").write_text("x")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "two")
    short = _git(repo, "rev-parse", "--short", "HEAD")
    out = _run(repo, "--dry-run", "--out", str(tmp_path / "images"))
    assert out.returncode == 0, out.stderr
    assert f"ragstack-tools-v3.2.1+{short}-b1.sif" in out.stdout


def test_dirty_tree_is_refused(repo, tmp_path):
    (repo / "python" / "ragstack" / "version.py").write_text("# broken\n")
    out = _run(repo, "--dry-run", "--out", str(tmp_path / "images"))
    assert out.returncode == 3
    assert "uncommitted" in out.stderr and "refusing to build" in out.stderr


def test_sandbox_mode_prints_the_two_step(repo, tmp_path):
    out = _run(repo, "--dry-run", "--sandbox", "--out", str(tmp_path / "images"))
    assert out.returncode == 0, out.stderr
    assert "apptainer build --sandbox --build-arg VERSION=v3.2.1" in out.stdout
    assert re.search(r"^then:       apptainer build .*ragstack-tools-v3\.2\.1-b1\.sif .*\.sbx", out.stdout, re.M)


def test_def_writes_the_public_version_into_pyproject_before_pip():
    """pyproject.toml tracks the SERVER release tag S, which may differ from the
    tools tag T the image is built at; the def rewrites the staged copy from
    VERSION before `pip install` and asserts the wheel metadata equals
    __version__'s public part (L4 of the #664 review)."""
    text = DEF.read_text()
    post = text[text.index("%post"):text.index("%environment")]
    sed = post.index('sed -i -E "s/^version = ')
    assert 'PUBLIC="{{ VERSION }}"; PUBLIC="${PUBLIC#v}"; PUBLIC="${PUBLIC%%+*}"' in post[:sed]
    assert sed < post.index('python -m pip install "/opt/ragstack/python[')
    assert 'assert version("ragstack") == expected.split("+")[0]' in post


@pytest.mark.skipif(not os.environ.get("RAGSTACK_BUILD_TESTS"),
                    reason="real apptainer build (fakeroot + network); RAGSTACK_BUILD_TESTS=1 opts in")
def test_real_build_of_a_tiny_def_verifies_and_writes_the_receipt(repo, tmp_path):
    if shutil.which("apptainer") is None:
        pytest.skip("no apptainer")
    (repo / "apptainer" / "ragstack-tools.def").write_text(
        "Bootstrap: docker\nFrom: alpine:3.20\n\n%files\n    {{ SRC }} /opt/ragstack/python\n\n%post\n"
        "    mkdir -p /opt/ragstack\n"
        "    printf 'version=%s\\ncommit=%s\\nbuild=%s\\nbuild_date=%s\\n' "
        "'{{ VERSION }}' '{{ COMMIT }}' '{{ BUILD }}' '{{ BUILD_DATE }}' > /opt/ragstack/RELEASE\n\n"
        "%labels\n    org.ragstack.version {{ VERSION }}\n    org.ragstack.commit {{ COMMIT }}\n"
        "    org.ragstack.build {{ BUILD }}\n    org.ragstack.build-date {{ BUILD_DATE }}\n")
    _git(repo, "commit", "-q", "-am", "tiny def")
    _git(repo, "tag", "-f", "v3.2.1")
    # Present in the working tree, gitignored, must NOT ship (M1 of the #664 review).
    (repo / "python" / "secrets.env").write_text("gitignored")
    (repo / "python" / "ragstack" / "__pycache__").mkdir()
    (repo / "python" / "ragstack" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    out_dir = tmp_path / "images"
    out = _run(repo, "--out", str(out_dir), env={"TMPDIR": str(tmp_path)})
    assert out.returncode == 0, out.stderr + out.stdout
    sif = out_dir / "ragstack-tools-v3.2.1-b1.sif"
    receipt = json.loads((out_dir / "ragstack-tools-v3.2.1-b1.sif.receipt.json").read_text())
    assert receipt["name"] == sif.name and receipt["version"] == "v3.2.1" and receipt["build"] == "1"
    digest = subprocess.run(["sha256sum", str(sif)], check=True, capture_output=True,
                            text=True).stdout.split()[0]
    assert receipt["sha256"] == digest
    labels = subprocess.run(["apptainer", "inspect", "--labels", str(sif)], check=True,
                            capture_output=True, text=True).stdout
    assert "org.ragstack.version: v3.2.1" in labels
    assert f"org.ragstack.commit: {receipt['commit']}" in labels
    assert "labels ok" in out.stdout and "RELEASE ok" in out.stdout
    listing = subprocess.run(
        ["apptainer", "exec", str(sif), "find", "/opt/ragstack/python", "-type", "f"],
        check=True, capture_output=True, text=True).stdout.split()
    assert "/opt/ragstack/python/ragstack/version.py" in listing  # committed: ships
    assert not any("secrets.env" in p or "__pycache__" in p for p in listing), listing
    assert sorted(p.removeprefix("/opt/ragstack/") for p in listing) == \
        sorted(_git(repo, "ls-files", "python").splitlines())
    assert not list(tmp_path.glob("ragstack-tools-src.*")), "staging dir must be cleaned up"
    # A second build of the same version is b2.
    out2 = _run(repo, "--dry-run", "--out", str(out_dir))
    assert "ragstack-tools-v3.2.1-b2.sif" in out2.stdout
