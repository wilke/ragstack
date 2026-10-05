"""Build and runtime identity of THIS process — the repo version (ADR-0010
decision 1) and the body of ``GET /v1/version``.

This module is the **single derivation** of the repo version. ``git describe``
runs in exactly one place, :func:`_describe`, and its raw output never leaves
it: callers get a parsed :class:`Described` (tag, distance, sha, dirty) and
compose what they need from that. The build script
(``apptainer/build-tools-image.sh`` → ``python -m ragstack.version --shell``),
``ragstack.__version__`` and the version endpoint all go through it. Inside the
tools image there is no git and no repository; the build writes
``ragstack/_release.py`` and that is read instead.

The repo version
----------------

* ``vX`` when ``HEAD`` is exactly on a release tag (``git describe`` says
  ``vX-0-g<sha>``);
* ``vX+<shortsha>`` otherwise — "commit ``<sha>``; the last release before it
  was ``vX``" — which is what ``dev`` on ``main`` derives;
* a **dirty** tree has no version: :func:`derive_version` raises
  :class:`DirtyTreeError` and the build exits non-zero;
* no reachable ``v*`` tag: :class:`NoTagError`. Nothing invents a ``v0.0.0``.

No commit count, no ``-g``, never ``-dirty``. The ``+<sha>`` part is SemVer
build metadata / a PEP 440 *local version segment*: ``1.6.4+a2be96f`` is a
valid PEP 440 version (``packaging.version.Version`` parses it; a leading
``v`` is accepted and normalised away; ``.local == "a2be96f"``).

What PEP 440 does with the local segment, and why we never compare: SemVer
says build metadata is ignored for ordering; PEP 440 does **not** — a local
version sorts *after* its public version (``Version("1.6.4+abc") >
Version("1.6.4")``, and ``==`` is False) and before the next release
(``1.6.5 > 1.6.4+fffffff``), and two locals compare segment by segment
(``+abc < +abd``), which for commit shas is lexicographic noise. So the
public part orders releases correctly, and the local part orders nothing
meaningful. Nothing in this repository compares two derived versions — they
are identities, not ordinals: ``v1.6.4+a2be96f`` is not newer or older than
``v1.6.4``, it is a different commit — and ``tests/unit/test_version_derivation.py``
pins the PEP 440 behaviour so nobody starts.

``git describe --dirty`` sees **tracked** changes only; an untracked or
gitignored file is not "dirty". That is why the build script ships the
*commit's* ``python/`` (``git archive HEAD``), never the working tree's.

``ragstack.__version__`` and ``version``
----------------------------------------

``ragstack.__version__`` is :func:`package_version`: the repo version **as PEP
440** (``1.6.4+a2be96f`` — no leading ``v``; the image name keeps the ``v``),
resolved lazily on first access and cached for the process. Resolution order:

1. **A git checkout.** ``git`` is on ``PATH`` and the directory this package
   was imported from (``python/ragstack`` → repo root, two levels up) is the
   *top level* of a git working tree: derive. A dirty tree yields the derived
   version with a ``.dirty`` local suffix (``1.6.4+a2be96f.dirty`` /
   ``1.6.4+dirty``) rather than an exception — ``__version__`` is informational
   and must never fail an import, but it must never equal a buildable version
   when the tree is not what any build saw.
2. **A generated ``ragstack/_release.py``.** The tools-image build writes it
   into the installed package with the same four values it put in the image
   labels and ``/opt/ragstack/RELEASE``. Gitignored; never committed.
3. **The distribution version** (``importlib.metadata``, i.e. ``pyproject.toml``'s
   ``version``), or ``0.1.0`` when the package is not installed at all. The
   "I cannot tell" answer, and it looks like one.

``pyproject.toml`` keeps a *static* version: the public part of the last
release (``1.6.4``), bumped by hand when a release is tagged, and
``tests/unit/test_version_derivation.py`` asserts it equals the tag part of
the checkout's derived version — so the metadata and ``__version__`` agree on
the release and differ only in the local segment that names the commit.

Three artifacts are versioned separately (ADR-0010, three-artifact model):
a *tools image* is a build of the repo at a tag ``T`` (this module derives
``T`` or ``T+sha`` at build time); a *workflow* is CWL text naming a tools
image by name; a *server* release is a tag ``S`` that chooses which tools
image its CWL names. ``pyproject.toml`` and this module describe the repo
checkout — ``S`` for a server, ``T`` for a tools build — never "the image the
CWL names"; nothing here compares the two.

The endpoint
------------

``GET /v1/version`` reports ``version`` (the above), ``git_tag`` and ``git_sha``
(``contracts/schemas/version_response.json``). The control plane (ADR-0007)
needs to know *which code* a tenant API is running without reading the
tenant's worktree, so the API reports it. For ``git_tag``/``git_sha``, in order
of trust:

* ``RAGSTACK_GIT_TAG`` / ``RAGSTACK_GIT_SHA`` — set by the systemd unit the ctl
  renders from the registry's ``code{tag,sha}``. When present they win
  outright, because they describe the *artifact* the tenant was launched from,
  which is what an operator comparing "configured vs running" wants to see.
* the checkout this package was imported from — the hand-started
  (``supervisor: manual``) tenants and every dev server. ``git_tag`` is the
  ``git describe --tags --always --dirty`` *spelling* (``v1.6.4``,
  ``v1.6.4-22-g17425fd``, ``v1.6.4-dirty``, or a bare sha when no tag is
  reachable), composed from the one parsed describe; ``git_sha`` is
  ``git rev-parse --short HEAD``. Argv list only, never a shell; bounded
  timeout; any failure is ``null``, never an exception — a version endpoint
  must not be the thing that 500s.

**Which git, and which repository.** ``git`` is resolved once at import with
:func:`shutil.which` (``None`` → every git-derived field is null and no
subprocess is ever spawned), and the checkout is *proved* before anything git
says is believed: ``git rev-parse --show-toplevel`` must equal
:data:`_CHECKOUT`. A package installed into a conda env
(``envs/ragstack/lib/python3.12/site-packages/ragstack``) has a ``parents[2]``
that is not a repository, and git searches *upward* from its cwd — so without
that check the endpoint would report the tag of whatever unrelated repository
happens to enclose the env.

**Caching.** Successful lookups are cached for the life of the process: they
cannot change, and a subprocess per request would be an easy amplification lever
on an endpoint any credential can reach. Failures are *not* cached as values — a
transient ``git`` timeout must not become a permanent ``null`` — but they are
rate-limited to one attempt per :data:`RETRY_INTERVAL_S`, so a dead or hanging
git is not re-spawned on every request either. The cold path is serialised by
one module lock: N concurrent first requests spawn one git, not N.

The startup warm-up (:func:`warm_cache`) is the one caller that does NOT arm
that back-off. It runs before any request exists, so a warm-up that lost a race
with a cold page cache or a slow NFS ``git`` would otherwise pre-commit the
first real caller to :data:`RETRY_INTERVAL_S` of guaranteed nulls that nothing
had asked for.

Command line
------------

``python -m ragstack.version [--repo DIR] [--shell | --json]`` prints the
derived version (with ``--shell``/``--json`` also the full commit sha) and
exits 3 on a dirty tree, 4 when no ``v*`` tag is reachable, 2 when the
directory is not a repository or ``git`` is missing. This is the build's
version step.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

#: Captured at import, i.e. at process start — the API imports this module when
#: the router is wired. RFC 3339 UTC with a trailing ``Z``, the spelling the
#: control plane uses for every timestamp it renders (``+00:00`` and ``Z`` are
#: the same instant, but two spellings of one field is a diff nobody wants).
STARTED_AT: str = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

IMPL = "python"
FALLBACK_VERSION = "0.1.0"
GIT_TIMEOUT_S = 2.0
#: How long a failed lookup is remembered as "do not retry yet".
RETRY_INTERVAL_S = 60.0

#: The checkout this package lives in: ``python/ragstack/version.py`` → repo root.
#: ``git`` is run with this as its working directory, so a tenant whose worktree
#: differs from the operator's shell cwd still reports *its own* commit — and is
#: compared against ``--show-toplevel`` so an unrelated enclosing repository
#: cannot answer for it.
_CHECKOUT = Path(__file__).resolve().parents[2]

#: Absolute path to ``git``, resolved once. ``None`` on a host without it: every
#: git-derived field is then null and no lookup is attempted. Resolved at import
#: so a later ``PATH`` change cannot make the endpoint pick a different binary
#: mid-process.
_GIT: str | None = shutil.which("git")

#: The one ``git describe``. ``--match v*`` so only release tags count,
#: ``--long`` so the output always has the ``-<N>-g<sha>`` shape, ``--always``
#: so a tagless checkout yields a bare sha for ``git_tag`` instead of an error.
_DESCRIBE_ARGS: tuple[str, ...] = (
    "describe", "--tags", "--match", "v*", "--long", "--dirty", "--always",
)

#: ``vX-N-g<sha>[-dirty]``. The tag is matched non-greedily so a pre-release
#: tag containing ``-`` (``v1.7.0-rc1``) still parses: ``-<N>-g<sha>`` is
#: anchored at the end.
_DESCRIBE_RE = re.compile(
    r"^(?P<tag>v\S+?)-(?P<n>\d+)-g(?P<sha>[0-9a-f]{4,40})(?P<dirty>-dirty)?$"
)

#: A derived version: ``vX`` or ``vX+<shortsha>``. The stamping script and the
#: tree-wide pin test take a version apart with it instead of re-deriving.
VERSION_RE = re.compile(r"^(?P<tag>v[0-9][^+\s]*)(?:\+(?P<sha>[0-9a-f]{4,40}))?$")

#: A release tag: ``v`` + a PEP 440 public version (epoch, release segment,
#: optional pre/post/dev with PEP 440's permitted separators). ``v-next`` and
#: ``v1.6.4_hotfix`` fail; ``v1.6.4``, ``v1.7.0-rc1``, ``v2.0.0.post1`` pass.
#: Checked once, in :attr:`Described.version`, so no caller re-validates.
_TAG_RE = re.compile(
    r"^v(?:\d+!)?\d+(?:\.\d+)*"
    r"(?:[-_.]?(?:a|b|c|rc|alpha|beta|pre|preview)[-_.]?\d*)?"
    r"(?:[-_.]?(?:post|rev|r)[-_.]?\d*)?"
    r"(?:[-_.]?dev[-_.]?\d*)?$",
    re.IGNORECASE,
)

_LOCK = threading.Lock()
#: argv tuple → output. **Successes only** — see the module docstring.
_CACHE: dict[tuple[str, ...], str] = {}
#: argv tuple → monotonic deadline before which a retry is pointless.
_RETRY_AFTER: dict[tuple[str, ...], float] = {}


class VersionError(RuntimeError):
    """The version could not be derived from the repository."""


class DirtyTreeError(VersionError):
    """The working tree has uncommitted changes; nothing buildable has a version."""


class NoTagError(VersionError):
    """No ``v*`` tag is reachable from ``HEAD``; no version can be derived."""


@dataclass(frozen=True)
class Described:
    """What the one ``git describe`` said, parsed. ``tag`` is ``None`` when no
    ``v*`` tag is reachable (``--always`` then gave a bare sha in ``sha``)."""

    tag: str | None
    distance: int
    sha: str
    dirty: bool

    @property
    def version(self) -> str:
        """``vX`` or ``vX+<shortsha>``; raises :class:`NoTagError` without a tag and
        :class:`VersionError` for a ``v*`` tag that is not version-shaped
        (``v-next``, ``v1.6.4_hotfix``): the tag must be ``v`` + a PEP 440 public
        version, or the image name and ``__version__`` it feeds are not versions."""
        if self.tag is None:
            raise NoTagError(
                f"no v* tag is reachable from HEAD (git describe --always gave {self.sha}). "
                "A version is a tag or a tag plus a commit; nothing invents one."
            )
        if _TAG_RE.match(self.tag) is None:
            raise VersionError(
                f"tag {self.tag!r} is not version-shaped: a release tag is 'v' followed by a "
                "PEP 440 public version (v1.6.4, v1.7.0-rc1, v2.0.0.post1); re-tag."
            )
        return self.tag if self.distance == 0 else f"{self.tag}+{self.sha}"

    @property
    def legacy_spelling(self) -> str:
        """What ``git describe --tags --always --dirty`` would have printed —
        the ``git_tag`` field's contract — composed, not passed through."""
        suffix = "-dirty" if self.dirty else ""
        if self.tag is None:
            return self.sha + suffix
        if self.distance == 0:
            return self.tag + suffix
        return f"{self.tag}-{self.distance}-g{self.sha}{suffix}"


def parse_describe(raw: str) -> Described | None:
    """Parse one line of the describe output; ``None`` when it is neither the
    long form nor a bare sha (the ``--always`` fallback)."""
    raw = raw.strip()
    m = _DESCRIBE_RE.match(raw)
    if m is not None:
        return Described(
            tag=m.group("tag"), distance=int(m.group("n")), sha=m.group("sha"),
            dirty=bool(m.group("dirty")),
        )
    m2 = re.match(r"^(?P<sha>[0-9a-f]{4,40})(?P<dirty>-dirty)?$", raw)
    if m2 is not None:
        return Described(tag=None, distance=0, sha=m2.group("sha"), dirty=bool(m2.group("dirty")))
    # The exact-tag spelling without ``--long`` (``v1.6.4``, ``v1.6.4-dirty``):
    # never what our own describe prints, but what a supervisor's
    # ``RAGSTACK_GIT_TAG`` carries when the launched worktree sits on a tag.
    m3 = re.match(r"^(?P<tag>v[0-9]\S*?)(?P<dirty>-dirty)?$", raw)
    if m3 is not None:
        return Described(tag=m3.group("tag"), distance=0, sha="", dirty=bool(m3.group("dirty")))
    return None


def split_version(version: str) -> tuple[str, str | None]:
    """``"v1.6.4+a2be96f"`` → ``("v1.6.4", "a2be96f")``; ``"v1.6.4"`` → ``("v1.6.4", None)``.
    Raises ``ValueError`` for anything that is not a derived version."""
    m = VERSION_RE.match(version or "")
    if m is None or re.search(r"-\d+-g[0-9a-f]{4,40}$|-dirty$", m.group("tag")):
        # The second clause rejects a raw describe string (`v1.6.4-22-g17425fd`,
        # `…-dirty`) masquerading as a tag: those never leave the derivation.
        raise ValueError(f"{version!r} is not a derived version (vX or vX+<sha>)")
    return m.group("tag"), m.group("sha")


def pep440(version: str) -> str:
    """The derived version as PEP 440: the leading ``v`` dropped (``1.6.4+a2be96f``)."""
    return version[1:] if version.startswith("v") else version


def cache_clear() -> None:
    """Forget every cached lookup and back-off. The test seam."""
    with _LOCK:
        _CACHE.clear()
        _RETRY_AFTER.clear()


# --------------------------------------------------------------------------- #
# git plumbing (argv only, bounded, cached)
# --------------------------------------------------------------------------- #


def _run_git(git: str, *args: str, cwd: Path = _CHECKOUT,
             timeout: float = GIT_TIMEOUT_S) -> str | None:
    """One ``git`` invocation; ``None`` on any failure."""
    try:
        proc = subprocess.run(  # argv list, no shell — never `shell=True` here
            [git, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    return out or None


def _git(*args: str, arm_backoff: bool = True) -> str | None:
    """Cached ``git <args>``; ``None`` on any failure or empty output.

    The lock is held across the subprocess on purpose: the first request after
    a restart is a cache miss for every concurrent caller, and letting each of
    them fork its own ``git`` is the amplification the cache exists to prevent.
    The call is bounded by :data:`GIT_TIMEOUT_S`, and it runs in the threadpool
    (the route is a plain ``def``), never on the event loop.

    *arm_backoff* is what separates a REQUEST from the startup warm-up. A
    request that finds git dead should stop everyone re-spawning it for
    :data:`RETRY_INTERVAL_S`; the warm-up should not, because it runs once,
    before any caller exists, at the moment the process is busiest — so a
    warm-up that lost a race with a slow disk would hand the first real
    request a minute of guaranteed nulls that nothing had asked for. It still
    CONSUMES an existing back-off (no point spawning into a known-dead git).
    """
    git = _GIT
    if git is None:
        return None
    with _LOCK:
        hit = _CACHE.get(args)
        if hit is not None:
            return hit
        now = time.monotonic()
        if now < _RETRY_AFTER.get(args, 0.0):
            # A recent failure: not cached as a value (the next call after the
            # back-off tries again) but not re-spawned per request either.
            return None
        out = _run_git(git, *args)
        if out is None:
            if arm_backoff:
                _RETRY_AFTER[args] = now + RETRY_INTERVAL_S
        else:
            _CACHE[args] = out
            _RETRY_AFTER.pop(args, None)
        return out


def git_is_this_checkout(*, arm_backoff: bool = True) -> bool:
    """Whether ``git`` in :data:`_CHECKOUT` answers for *this* checkout.

    ``git`` searches upward from its cwd, so a package installed outside a
    repository (a conda env, a wheel in site-packages) would otherwise report
    the identity of whatever repository encloses it.
    """
    top = _git("rev-parse", "--show-toplevel", arm_backoff=arm_backoff)
    if not top:
        return False
    try:
        return Path(top).resolve() == _CHECKOUT
    except OSError:
        return False


def _describe(*, arm_backoff: bool = True) -> Described | None:
    """The one ``git describe`` of this checkout, parsed; ``None`` when git is
    missing, the checkout is not proven, or the output is not describe-shaped."""
    if not git_is_this_checkout(arm_backoff=arm_backoff):
        return None
    raw = _git(*_DESCRIBE_ARGS, arm_backoff=arm_backoff)
    if raw is None:
        return None
    return parse_describe(raw)


# --------------------------------------------------------------------------- #
# The repo version
# --------------------------------------------------------------------------- #


def describe_repo(repo_root: Path) -> Described:
    """Uncached, explicit-root derivation for the build script and the tests.

    ``repo_root`` must be the *top level* of a git working tree (not merely
    inside one). Raises :class:`VersionError` when git is missing, the
    directory is not a checkout, or the output does not parse; the returned
    :class:`Described` may still carry ``dirty`` or no tag — those are
    :func:`derive_version`'s refusals.
    """
    repo_root = Path(repo_root)
    git = shutil.which("git")
    if git is None:
        raise VersionError("git is not on PATH; the version cannot be derived")
    top = _run_git(git, "rev-parse", "--show-toplevel", cwd=repo_root, timeout=10.0)
    try:
        ok = top is not None and Path(top).resolve() == repo_root.resolve()
    except OSError:
        ok = False
    if not ok:
        raise VersionError(f"{repo_root} is not the top level of a git working tree")
    raw = _run_git(git, *_DESCRIBE_ARGS, cwd=repo_root, timeout=10.0)
    if raw is None:
        raise VersionError(f"git describe failed in {repo_root}")
    d = parse_describe(raw)
    if d is None:
        raise VersionError(f"unexpected git describe output in {repo_root}")
    return d


def derive_version(repo_root: Path) -> str:
    """The repo version of the checkout at ``repo_root``: ``vX`` on a release
    tag, ``vX+<shortsha>`` past one. Raises :class:`DirtyTreeError` on a dirty
    tree, :class:`NoTagError` when no ``v*`` tag is reachable, and
    :class:`VersionError` when the directory is not a checkout."""
    d = describe_repo(repo_root)
    if d.dirty:
        raise DirtyTreeError(
            f"{repo_root} has uncommitted changes (git describe says -dirty): a build "
            "from a dirty tree has no version. Commit or stash, then retry."
        )
    return d.version


def commit_sha(repo_root: Path) -> str:
    """Full 40-hex sha of ``HEAD`` at ``repo_root`` (for the image's commit label)."""
    git = shutil.which("git")
    if git is None:
        raise VersionError("git is not on PATH")
    out = _run_git(git, "rev-parse", "HEAD", cwd=Path(repo_root), timeout=10.0)
    if out is None or not re.fullmatch(r"[0-9a-f]{40}", out):
        raise VersionError(f"git rev-parse HEAD failed in {repo_root}")
    return out


def _release_file_version() -> str | None:
    """Resolution step 2: the generated ``ragstack/_release.py``, if the build wrote one."""
    try:
        from ragstack import _release  # type: ignore[attr-defined]
    except ImportError:
        return None
    value = getattr(_release, "VERSION", "")
    return pep440(str(value)) if value else None


def _distribution_version() -> str:
    """Resolution step 3: whatever the installed distribution says."""
    try:
        return metadata.version("ragstack")
    except metadata.PackageNotFoundError:
        return FALLBACK_VERSION


def package_version(*, arm_backoff: bool = True) -> str:
    """``ragstack.__version__`` — the repo version as PEP 440, by the resolution
    order in the module docstring. Cached through the same git cache as the
    endpoint's other fields, so a process derives once.

    When the supervisor set ``RAGSTACK_GIT_TAG`` (the ctl renders the
    registry's ``code.tag``, itself a describe spelling of the launched
    worktree), that word wins here too and git is not consulted: the version
    is derived from the override when it is describe-shaped, else the
    generated/distribution fallbacks answer.
    """
    override = _env("RAGSTACK_GIT_TAG")
    if override:
        d = parse_describe(override)
    else:
        d = _describe(arm_backoff=arm_backoff)
    if d is not None and d.tag is not None:
        try:
            v = pep440(d.version)
        except VersionError:
            # A mis-shaped tag: informational here, so fall through rather
            # than fail an import or 500 the version endpoint.
            v = ""
        if v:
            if d.dirty:
                v += ".dirty" if "+" in v else "+dirty"
            return v
    return _release_file_version() or _distribution_version()


# --------------------------------------------------------------------------- #
# The endpoint
# --------------------------------------------------------------------------- #


def _env(env_var: str) -> str | None:
    override = os.environ.get(env_var, "").strip()
    return override or None


def git_tag(*, arm_backoff: bool = True) -> str | None:
    """``RAGSTACK_GIT_TAG``, else the describe spelling of this checkout, else null."""
    override = _env("RAGSTACK_GIT_TAG")
    if override:
        return override
    d = _describe(arm_backoff=arm_backoff)
    if d is None:
        # Not describe-shaped (a fake, an exotic tag): the raw line is still
        # the ``--always`` answer git gave for this checkout, and the field's
        # contract is "what describe printed".
        if git_is_this_checkout(arm_backoff=arm_backoff):
            return _git(*_DESCRIBE_ARGS, arm_backoff=arm_backoff)
        return None
    return d.legacy_spelling


def git_sha(*, arm_backoff: bool = True) -> str | None:
    override = _env("RAGSTACK_GIT_SHA")
    if override:
        return override
    if not git_is_this_checkout(arm_backoff=arm_backoff):
        return None
    return _git("rev-parse", "--short", "HEAD", arm_backoff=arm_backoff)


def version_info(*, arm_backoff: bool = True) -> dict[str, Any]:
    """The ``VersionResponse`` body (``contracts/schemas/version_response.json``).

    Synchronous, and on the very first call possibly slow (up to three bounded
    ``git`` runs). The API warms it in its lifespan and serves the route from
    the threadpool, so neither the event loop nor a request ever waits on a
    cold cache — see ``api/deps.py`` and ``api/routers/version.py``.
    """
    return {
        "version": package_version(arm_backoff=arm_backoff),
        "git_tag": git_tag(arm_backoff=arm_backoff),
        "git_sha": git_sha(arm_backoff=arm_backoff),
        "started_at": STARTED_AT,
        "python": platform.python_version(),
        "impl": IMPL,
    }


def warm_cache() -> dict[str, Any]:
    """Populate the cache off the request path. **Never arms the back-off.**

    Called once from the API lifespan, in a worker thread, as a fire-and-forget
    task (``api/deps.py``). Two properties it must have and ``version_info()``
    must not:

    * it does not arm the failure back-off, so a warm-up that loses a race
      with a cold page cache or a slow NFS ``git`` cannot hand the first real
      request :data:`RETRY_INTERVAL_S` of guaranteed nulls;
    * it swallows nothing extra — ``version_info`` already turns every git
      failure into ``None`` — so the caller's only job is to not let a
      warm-up failure keep the API down.
    """
    return version_info(arm_backoff=False)


# --------------------------------------------------------------------------- #
# Command line: the build's version step
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m ragstack.version",
        description="Derive the repo version (vX or vX+<shortsha>) of a git checkout.",
    )
    ap.add_argument("--repo", type=Path, default=_CHECKOUT,
                    help=f"repository top level (default: this package's checkout, {_CHECKOUT})")
    out = ap.add_mutually_exclusive_group()
    out.add_argument("--shell", action="store_true",
                     help="print VERSION=… and COMMIT=… lines for a shell to eval")
    out.add_argument("--json", action="store_true", help="print {version, commit} as JSON")
    args = ap.parse_args(argv)
    try:
        version = derive_version(args.repo)
        commit = commit_sha(args.repo)
    except DirtyTreeError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 3
    except NoTagError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 4
    except VersionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.shell:
        print(f"VERSION={version}")
        print(f"COMMIT={commit}")
    elif args.json:
        print(json.dumps({"version": version, "commit": commit}))
    else:
        print(version)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess in tests
    sys.exit(main())
