"""The repo version (ADR-0010 decision 1): ``ragstack.version.derive_version``.

One function runs the one ``git describe``; the raw string never leaves it.
Each case below builds a real temporary repository and asks for its version,
so the mapping is tested against git, not against a regex of our own:

* exact tag             ``v1.0.0-0-g<sha>``  → ``v1.0.0``
* N commits past a tag  ``v1.0.0-N-g<sha>``  → ``v1.0.0+<sha>``
* dirty tree            ``…-dirty``          → ``DirtyTreeError``
* no ``v*`` tag                               → ``NoTagError`` (no invented ``v0.0.0``)

Plus the PEP 440 facts the ADR relies on, pinned against ``packaging`` so a
future reader cannot "fix" the local segment into an ordinal.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from packaging.version import Version

from ragstack import version as v

REPO = Path(__file__).resolve().parents[3]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A fresh repository with one commit and no tag."""
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.invalid")
    _git(r, "config", "user.name", "t")
    (r / "f").write_text("one\n")
    _git(r, "add", "f")
    _git(r, "commit", "-q", "-m", "one")
    return r


def _commit(repo: Path, text: str) -> None:
    (repo / "f").write_text(text)
    _git(repo, "commit", "-q", "-am", text)


# --------------------------------------------------------------------------- #
# the matrix
# --------------------------------------------------------------------------- #


def test_exact_tag_is_the_tag(repo):
    _git(repo, "tag", "v1.0.0")
    assert v.derive_version(repo) == "v1.0.0"


def test_commits_past_a_tag_append_the_short_sha(repo):
    _git(repo, "tag", "v1.0.0")
    _commit(repo, "two")
    _commit(repo, "three")
    short = _git(repo, "rev-parse", "--short", "HEAD")
    assert v.derive_version(repo) == f"v1.0.0+{short}"
    # The describe string's commit count and `-g` never leave the function.
    assert "-2-g" not in v.derive_version(repo)


def test_dirty_tree_is_refused(repo):
    _git(repo, "tag", "v1.0.0")
    (repo / "f").write_text("edited, uncommitted\n")
    with pytest.raises(v.DirtyTreeError, match="uncommitted"):
        v.derive_version(repo)
    # The same tree, committed, has a version again.
    _git(repo, "commit", "-q", "-am", "two")
    assert v.derive_version(repo).startswith("v1.0.0+")


def test_untracked_file_is_not_dirty_to_git_describe(repo):
    """Documented, not desired: `--dirty` sees tracked changes only. That is
    why the build script stages python/ from the commit (`git archive HEAD`)
    instead of the working tree."""
    _git(repo, "tag", "v1.0.0")
    (repo / "stray").write_text("x")
    assert v.derive_version(repo) == "v1.0.0"


def test_no_v_tag_is_refused_not_invented(repo):
    with pytest.raises(v.NoTagError):
        v.derive_version(repo)
    # A tag that does not match v* does not count either.
    _git(repo, "tag", "release-1")
    with pytest.raises(v.NoTagError):
        v.derive_version(repo)
    _git(repo, "tag", "v0.1.0")
    assert v.derive_version(repo) == "v0.1.0"


@pytest.mark.parametrize("tag", ["v-next", "v1.6.4_hotfix", "vX", "v1.6.4-hotfix"])
def test_a_v_tag_that_is_not_version_shaped_is_refused(repo, tag):
    """`--match v*` would pick these up; the version they would feed is not a
    version. Refused once, in Described.version, as VersionError (not NoTag)."""
    _git(repo, "tag", tag)
    with pytest.raises(v.VersionError, match="not version-shaped") as info:
        v.derive_version(repo)
    assert not isinstance(info.value, v.NoTagError)
    # __version__ falls through to the distribution rather than raising.
    d = v.describe_repo(repo)
    with pytest.raises(v.VersionError):
        _ = d.version
    v.cache_clear()

    def fake_describe(*, arm_backoff: bool = True) -> v.Described:
        return d

    original = v._describe
    v._describe = fake_describe  # type: ignore[assignment]
    try:
        assert v.package_version() == v._distribution_version()
    finally:
        v._describe = original
        v.cache_clear()


@pytest.mark.parametrize("tag", ["v1.6.4-rc1", "v1.6.4rc1", "v1.6.4.post1", "v2.0.0.dev3", "v1!2.0"])
def test_pep440_shaped_pre_post_dev_tags_are_accepted(repo, tag):
    _git(repo, "tag", tag)
    assert v.derive_version(repo) == tag


def test_prerelease_tag_with_a_dash_parses(repo):
    _git(repo, "tag", "v1.1.0-rc1")
    assert v.derive_version(repo) == "v1.1.0-rc1"
    _commit(repo, "two")
    short = _git(repo, "rev-parse", "--short", "HEAD")
    assert v.derive_version(repo) == f"v1.1.0-rc1+{short}"


def test_not_a_checkout_is_an_error(tmp_path):
    with pytest.raises(v.VersionError, match="top level"):
        v.derive_version(tmp_path)


def test_a_subdirectory_of_a_checkout_is_not_the_checkout(repo):
    """`git` searches upward; the derivation must not answer for an enclosing repo."""
    sub = repo / "python"
    sub.mkdir()
    _git(repo, "tag", "v1.0.0")
    with pytest.raises(v.VersionError, match="top level"):
        v.derive_version(sub)


def test_commit_sha_is_the_full_head(repo):
    assert v.commit_sha(repo) == _git(repo, "rev-parse", "HEAD")
    assert len(v.commit_sha(repo)) == 40


def test_split_and_pep440():
    assert v.split_version("v1.6.4") == ("v1.6.4", None)
    assert v.split_version("v1.6.4+a2be96f") == ("v1.6.4", "a2be96f")
    assert v.pep440("v1.6.4+a2be96f") == "1.6.4+a2be96f"
    with pytest.raises(ValueError):
        v.split_version("v1.6.4-22-g17425fd")  # a raw describe string is not a version
    with pytest.raises(ValueError):
        v.split_version("1.6.4")


# --------------------------------------------------------------------------- #
# the command line (the build script's version step)
# --------------------------------------------------------------------------- #


def _cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ragstack.version", "--repo", str(repo), *args],
        capture_output=True, text=True, cwd=REPO / "python",
        env={"PYTHONPATH": str(REPO / "python"), "PATH": subprocess.os.environ["PATH"]},
    )


def test_cli_prints_version_and_shell_form(repo):
    _git(repo, "tag", "v2.0.0")
    _commit(repo, "two")
    short = _git(repo, "rev-parse", "--short", "HEAD")
    full = _git(repo, "rev-parse", "HEAD")
    assert _cli(repo).stdout.strip() == f"v2.0.0+{short}"
    out = _cli(repo, "--shell")
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == [f"VERSION=v2.0.0+{short}", f"COMMIT={full}"]


def test_cli_exit_codes(repo):
    assert _cli(repo).returncode == 4  # no tag
    _git(repo, "tag", "v2.0.0")
    (repo / "f").write_text("dirty\n")
    out = _cli(repo)
    assert out.returncode == 3 and "refused" in out.stderr and "uncommitted" in out.stderr
    assert _cli(repo.parent).returncode == 2  # not a checkout


# --------------------------------------------------------------------------- #
# ragstack.__version__
# --------------------------------------------------------------------------- #


def test_dunder_version_is_the_derived_pep440_version_of_this_checkout():
    """In this checkout `__version__` is the derived version as PEP 440 — a
    dirty tree (a developer running the suite mid-edit) is marked, never
    silently equal to a buildable version."""
    import ragstack

    d = v.describe_repo(REPO)
    expected = v.pep440(d.version)
    if d.dirty:
        expected += ".dirty" if "+" in expected else "+dirty"
    assert ragstack.__version__ == expected
    assert v.package_version() == expected
    Version(ragstack.__version__)  # PEP 440 valid, dirty or not


def test_pyproject_carries_the_public_part_of_the_last_release():
    """pyproject.toml is static and holds the last release's public version;
    the stamping step bumps it. The metadata and __version__ therefore agree
    on the release and differ only in the local segment naming the commit."""
    import tomllib

    try:
        tag, _sha = v.split_version(v.describe_repo(REPO).version)
    except v.NoTagError:
        pytest.skip("no v* tag reachable (shallow clone?)")
    static = tomllib.loads((REPO / "python" / "pyproject.toml").read_text())["project"]["version"]
    assert Version(static).public == Version(v.pep440(tag)).public


def test_supervisor_override_derives_without_git(monkeypatch):
    monkeypatch.setenv("RAGSTACK_GIT_TAG", "v1.6.4-22-g17425fd")
    v.cache_clear()
    monkeypatch.setattr(v.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert v.package_version() == "1.6.4+17425fd"
    monkeypatch.setenv("RAGSTACK_GIT_TAG", "v1.6.4")
    assert v.package_version() == "1.6.4"
    monkeypatch.setenv("RAGSTACK_GIT_TAG", "v1.6.4-dirty")
    assert v.package_version() == "1.6.4+dirty"
    v.cache_clear()


# --------------------------------------------------------------------------- #
# PEP 440 facts (pinned against packaging)
# --------------------------------------------------------------------------- #


def test_pep440_local_version_is_valid_and_v_normalises():
    pv = Version("1.6.4+abc1234")
    assert pv.public == "1.6.4" and pv.local == "abc1234"
    assert Version("v1.6.4+abc1234") == pv and str(Version("v1.6.4+abc1234")) == "1.6.4+abc1234"
    Version("1.6.4+abc1234.dirty")
    Version("1.6.4+dirty")


def test_pep440_local_segment_does_not_order_commits():
    """PEP 440 sorts a local version AFTER its public version and before the
    next release, and compares two locals segment-wise. The public part
    orders releases; the local part orders nothing we mean. Never compare."""
    assert Version("1.6.4+abc") != Version("1.6.4")
    assert Version("1.6.4+abc") > Version("1.6.4")
    assert Version("1.6.5") > Version("1.6.4+fffffff")
    # Two commits: lexicographic on the sha, which says nothing about history.
    assert Version("1.6.4+abc") < Version("1.6.4+abd")
    assert Version("1.6.4+abc") != Version("1.6.4+abd")
