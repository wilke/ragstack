"""Build and runtime identity of THIS process — the body of ``GET /v1/version``.

The control plane (ADR-0007) needs to know *which code* a tenant API is running
without reading the tenant's worktree: the ctl never queries a tenant's files,
only its registered origin. So the API reports it.

Where the values come from, in order of trust:

* ``RAGSTACK_GIT_TAG`` / ``RAGSTACK_GIT_SHA`` — set by the systemd unit the ctl
  renders from the registry's ``code{tag,sha}``. When present they win outright,
  because they describe the *artifact* the tenant was launched from, which is
  what an operator comparing "configured vs running" wants to see.
* ``git describe`` / ``git rev-parse`` run in the checkout this package was
  imported from — the hand-started (``supervisor: manual``) tenants and every dev
  server. Argv list only, never a shell; 2 s timeout; any failure is ``null``,
  never an exception — a version endpoint must not be the thing that 500s.
* ``importlib.metadata`` for the package version, ``0.1.0`` when the package is
  not installed (a bare ``PYTHONPATH`` checkout).

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
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import threading
import time
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

_LOCK = threading.Lock()
#: argv tuple → output. **Successes only** — see the module docstring.
_CACHE: dict[tuple[str, ...], str] = {}
#: argv tuple → monotonic deadline before which a retry is pointless.
_RETRY_AFTER: dict[tuple[str, ...], float] = {}


def cache_clear() -> None:
    """Forget every cached lookup and back-off. The test seam."""
    with _LOCK:
        _CACHE.clear()
        _RETRY_AFTER.clear()


def package_version() -> str:
    try:
        return metadata.version("ragstack")
    except metadata.PackageNotFoundError:
        return FALLBACK_VERSION


def _run_git(git: str, *args: str) -> str | None:
    """One ``git`` invocation in the checkout; ``None`` on any failure."""
    try:
        proc = subprocess.run(  # argv list, no shell — never `shell=True` here
            [git, *args],
            cwd=_CHECKOUT,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
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


def _env_or_git(env_var: str, *git_args: str, arm_backoff: bool = True) -> str | None:
    override = os.environ.get(env_var, "").strip()
    if override:
        return override
    if not git_is_this_checkout(arm_backoff=arm_backoff):
        return None
    return _git(*git_args, arm_backoff=arm_backoff)


def git_tag(*, arm_backoff: bool = True) -> str | None:
    return _env_or_git(
        "RAGSTACK_GIT_TAG", "describe", "--tags", "--always", "--dirty",
        arm_backoff=arm_backoff,
    )


def git_sha(*, arm_backoff: bool = True) -> str | None:
    return _env_or_git(
        "RAGSTACK_GIT_SHA", "rev-parse", "--short", "HEAD", arm_backoff=arm_backoff
    )


def version_info(*, arm_backoff: bool = True) -> dict[str, Any]:
    """The ``VersionResponse`` body (``contracts/schemas/version_response.json``).

    Synchronous, and on the very first call possibly slow (up to three bounded
    ``git`` runs). The API warms it in its lifespan and serves the route from
    the threadpool, so neither the event loop nor a request ever waits on a
    cold cache — see ``api/deps.py`` and ``api/routers/version.py``.
    """
    return {
        "version": package_version(),
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
