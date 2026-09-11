"""The conformance root conftest's control-plane guard (``conformance/conftest.py``).

Two suites live under ``conformance/``: the tenant API's and the control
plane's. The guard decides which of them a given invocation collects, and both
of its failure modes point the same way — a suite that CREATES AND DELETES
TENANTS gets run against whatever ``ragstack-ctl serve`` the host has, which on
coconut is the live control plane holding every tenant's admin key.

* **C-1** — the guard read ``config.invocation_params.args``, the RAW argv, and
  treated every non-``-`` token as a path. ``pytest . -k ctl`` therefore
  "named" the control-plane suite with the word ``ctl`` that belonged to
  ``-k``. It now reads ``config.args``, what pytest itself resolved to
  collection targets, with option values already consumed.
* **B-6** — an exported ``RAGSTACK_CTL_URL`` used to be enough on its own, so a
  bare ``pytest conformance/`` on a shell already talking to the control plane
  swept the ctl suite in. The variable says WHICH daemon; it must not decide
  WHETHER a second suite runs.
* **C-3** — ``test_contract_static.py`` needs no server at all, so it is
  collected even on a sweep. Before, it ran nowhere automatically.

Lives here rather than in ``conformance/``: every file there is a black-box
HTTP test of a running server, and this one asserts about a conftest. The
``python`` CI job runs ``tests/unit``, which is the point — the guard is the
thing standing between a sweep and production.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

CONFORMANCE = Path(__file__).resolve().parents[3] / "conformance"


@pytest.fixture(scope="module")
def guard():
    """``conformance/conftest.py``, imported by path.

    It is a conftest, so it is not importable by name; and it imports
    ``personas`` from its own directory, so that directory goes on the path.
    """
    if str(CONFORMANCE) not in sys.path:
        sys.path.insert(0, str(CONFORMANCE))
    spec = importlib.util.spec_from_file_location(
        "conformance_root_conftest", CONFORMANCE / "conftest.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _config(args: list[str], *, raw: list[str] | None = None, cwd: Path | None = None):
    """A stand-in for ``pytest.Config``.

    ``args`` is what pytest resolved (``config.args``); ``raw`` is the argv it
    was resolved from (``invocation_params.args``). Keeping them separate is
    the whole point: the bug was reading the second where the first was meant.
    """
    return SimpleNamespace(
        args=list(args),
        invocation_params=SimpleNamespace(
            dir=cwd or CONFORMANCE, args=tuple(raw if raw is not None else args)
        ),
        stash={},
    )


# --------------------------------------------------------------------------- #
# C-1: option values are not paths
# --------------------------------------------------------------------------- #
def test_k_ctl_does_not_name_the_ctl_suite(guard) -> None:
    """``pytest . -k ctl`` selects BY NAME; it does not ask for the suite.

    The raw argv carries a bare ``ctl`` token (the ``-k`` value) that resolves
    to ``conformance/ctl`` from the conformance directory — which is exactly
    how this fired.
    """
    config = _config(["."], raw=[".", "-k", "ctl"])
    assert guard._names_ctl_suite(config) is False


@pytest.mark.parametrize(
    "value_taking",
    [["-k", "ctl"], ["-m", "ctl"], ["--deselect", "ctl"], ["-p", "ctl"]],
    ids=lambda v: v[0],
)
def test_no_option_value_is_read_as_a_path(guard, value_taking) -> None:
    config = _config(["."], raw=[".", *value_taking])
    assert guard._names_ctl_suite(config) is False


def test_naming_the_suite_still_counts(guard) -> None:
    for named in ("ctl", "ctl/", "./ctl", str(guard.CTL_SUITE)):
        assert guard._names_ctl_suite(_config([named])) is True, named


def test_naming_a_file_or_nodeid_inside_it_counts(guard) -> None:
    assert guard._names_ctl_suite(_config(["ctl/test_auth.py"])) is True
    assert guard._names_ctl_suite(_config(["ctl/test_auth.py::test_x"])) is True


def test_naming_the_parent_is_a_sweep_not_a_request(guard) -> None:
    """``pytest conformance/`` is the sweep the guard exists for."""
    repo = CONFORMANCE.parent
    assert guard._names_ctl_suite(_config(["conformance"], cwd=repo)) is False
    assert guard._names_ctl_suite(_config(["."], cwd=CONFORMANCE)) is False


# --------------------------------------------------------------------------- #
# B-6: the env var says WHICH daemon, not WHETHER to run
# --------------------------------------------------------------------------- #
def test_a_sweep_skips_the_ctl_suite_even_with_the_url_exported(guard, monkeypatch) -> None:
    monkeypatch.setenv("RAGSTACK_CTL_URL", "http://127.0.0.1:23990")
    config = _config(["."], cwd=CONFORMANCE)
    assert guard.pytest_ignore_collect(guard.CTL_SUITE / "test_auth.py", config) is True
    assert config.stash[guard._ctl_ignored] is True


def test_naming_the_suite_collects_it_with_or_without_the_url(guard, monkeypatch) -> None:
    monkeypatch.delenv("RAGSTACK_CTL_URL", raising=False)
    config = _config(["ctl"])
    # None = "no opinion", i.e. collect it. Without the URL the ctl_url fixture
    # then raises UsageError at run time — loudly, which is the design.
    assert guard.pytest_ignore_collect(guard.CTL_SUITE / "test_auth.py", config) is None
    assert config.stash == {}


# --------------------------------------------------------------------------- #
# C-3: the static contract file needs no server, so a sweep keeps it
# --------------------------------------------------------------------------- #
def test_a_sweep_still_collects_the_static_contract_tests(guard, monkeypatch) -> None:
    monkeypatch.delenv("RAGSTACK_CTL_URL", raising=False)
    config = _config(["."], cwd=CONFORMANCE)
    # The directory must not be ignored either: ignoring it stops pytest
    # descending, and the file below would go with it.
    assert guard.pytest_ignore_collect(guard.CTL_SUITE, config) is None
    assert guard.pytest_ignore_collect(guard.CTL_STATIC_TESTS, config) is None
    assert guard.CTL_STATIC_TESTS.is_file()


def test_untouched_paths_get_no_opinion(guard) -> None:
    config = _config(["."], cwd=CONFORMANCE)
    assert guard.pytest_ignore_collect(CONFORMANCE / "test_query.py", config) is None


def test_the_collection_note_says_what_was_left_out(guard) -> None:
    config = _config(["."], cwd=CONFORMANCE)
    assert guard.pytest_report_collectionfinish(config) == []
    config.stash[guard._ctl_ignored] = True
    (note,) = guard.pytest_report_collectionfinish(config)
    assert "RAGSTACK_CTL_URL" in note and guard.CTL_STATIC_TESTS.name in note
