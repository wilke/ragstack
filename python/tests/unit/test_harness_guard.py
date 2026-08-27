"""Meta-tests: the harness's own guards must fail *loudly* (#432).

Two guards are under test, and both have the same contract — **the loudness is
the feature**, so every case here runs the guarded thing in a real subprocess
and asserts on the exit code *and* the message text. A guard that fired but
printed nothing useful would be as bad as no guard: the point of #432 is that a
run which imports the wrong tree, or reaches a live cluster, says so in terms an
operator can act on.

1. ``tests/conftest.py``'s import-origin guard — the run dies before collection
   when ``ragstack`` came from outside this checkout, naming both paths;
   ``RAGSTACK_TEST_ALLOW_FOREIGN_IMPORT=1`` downgrades it to a warning that
   still names both paths; a clean run says nothing at all.
2. ``tests/integration/test_elasticsearch.py``'s opt-in — skips when
   ``RAGSTACK_TEST_ES_URL`` is unset, and *still* skips when the old
   ``TEST_ES_URL`` name is exported, which is the entire reason for the rename.

**Reproducing the wrong-import condition is fiddly and the obvious recipes do
not work.** Plain ``PYTHONPATH=<decoy>`` does not reproduce it: ``tests/`` is a
package, so pytest's prepend importmode inserts ``python/`` at ``sys.path[0]``,
ahead of anything ``PYTHONPATH`` contributes. Nor is it enough for a launcher
stub to ``sys.path.insert(0, decoy)`` before calling ``pytest.main()`` — pytest
re-inserts its basedir ahead of that too. The stub must insert the decoy **and
import ragstack from it**, so the wrong module is already in ``sys.modules``
when the guard looks. That is the real failure mode anyway (something imported
``ragstack`` before the guard ran), and it is why the guard inspects the
already-imported module instead of trying to influence resolution.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

#: ``python/`` — the tree under test, and pytest's rootdir.
CHECKOUT_ROOT = Path(__file__).resolve().parents[2]

#: The node the subprocess runs. Trivial on purpose: these meta-tests are about
#: what happens *before* collection, so the target must not be able to fail for
#: reasons of its own, and must not churn when the suite does.
TARGET = f"{Path(__file__).relative_to(CHECKOUT_ROOT)}::test_meta_target_is_trivial"

_STUB = """
import sys
sys.path.insert(0, {decoy!r})
import ragstack          # the wrong tree, already in sys.modules — the real shape
import pytest
sys.exit(pytest.main(sys.argv[1:]))
"""


def test_meta_target_is_trivial():
    """The node the subprocess cases below run. Asserting nothing is the point."""
    assert True


def _child_env(**overrides: str | None) -> dict[str, str]:
    """A controlled child environment: no inherited opt-ins, no inherited pins."""
    env = dict(os.environ)
    for var in ("RAGSTACK_TEST_ALLOW_FOREIGN_IMPORT", "RAGSTACK_TEST_ES_URL", "TEST_ES_URL"):
        env.pop(var, None)
    # Pin PYTHONPATH at the checkout, i.e. give the *correct* tree every possible
    # advantage. The decoy still wins in the cases below because it is already
    # imported — which is exactly the claim the guard makes.
    env["PYTHONPATH"] = str(CHECKOUT_ROOT)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def _run_with_decoy(tmp_path: Path, **env_overrides: str | None) -> subprocess.CompletedProcess:
    decoy_root = tmp_path / "decoy"
    (decoy_root / "ragstack").mkdir(parents=True)
    (decoy_root / "ragstack" / "__init__.py").write_text("# not the checkout\n")

    stub = textwrap.dedent(_STUB.format(decoy=str(decoy_root)))
    return subprocess.run(
        [sys.executable, "-c", stub, TARGET, "-q", "-p", "no:cacheprovider", "--no-header"],
        cwd=str(CHECKOUT_ROOT),
        env=_child_env(**env_overrides),
        capture_output=True,
        text=True,
        timeout=300,
    )


def _decoy_path(tmp_path: Path) -> str:
    return str(tmp_path / "decoy" / "ragstack")


# Match the guard's *labelled lines*, not the bare paths. A bare
# `str(CHECKOUT_ROOT) in out` is ambiently satisfied — pytest prints the rootdir,
# tracebacks carry absolute paths — so a guard that dropped "expected under"
# entirely still passed it. Verified: that mutant now fails.
def _imported_line(tmp_path: Path) -> str:
    return f"imported from: {_decoy_path(tmp_path)}"


_EXPECTED_LINE = f"expected under: {CHECKOUT_ROOT}"


# --------------------------------------------------------------------------
# 1. the import-origin guard
# --------------------------------------------------------------------------


def test_a_foreign_ragstack_import_fails_the_run_and_names_both_paths(tmp_path):
    """The acceptance criterion of #432, executed.

    Without this the failure mode is silent by construction: the run is *green*,
    it just proved nothing about this checkout. So the assertion is not merely
    "nonzero exit" — it is that the operator is told which code was imported and
    which was expected, because those two paths are the whole diagnosis.
    """
    result = _run_with_decoy(tmp_path)
    out = result.stdout + result.stderr

    assert result.returncode != 0, f"the guard did not fail the run:\n{out}"
    assert _imported_line(tmp_path) in out, f"the imported path is not named:\n{out}"
    assert _EXPECTED_LINE in out, f"the expected root is not named:\n{out}"
    # And it died before running anything, not after a green pass.
    assert "1 passed" not in out, f"the suite ran anyway:\n{out}"


def test_the_escape_hatch_warns_with_both_paths_instead_of_failing(tmp_path):
    """Deliberately testing an installed ``ragstack`` stays possible — but noisy.

    A silent escape hatch would reintroduce the defect for anyone who exported
    the variable once and forgot, so the warning still carries both paths.
    """
    result = _run_with_decoy(tmp_path, RAGSTACK_TEST_ALLOW_FOREIGN_IMPORT="1")
    out = result.stdout + result.stderr

    assert result.returncode == 0, f"the escape hatch did not let the run proceed:\n{out}"
    assert "1 passed" in out, f"the target test did not run:\n{out}"
    assert _imported_line(tmp_path) in out, f"the warning omits the imported path:\n{out}"
    assert _EXPECTED_LINE in out, f"the warning omits the expected root:\n{out}"


def test_a_clean_run_is_silent(tmp_path):
    """The control. A guard that fires on correct runs would be turned off in a week."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", TARGET, "-q", "-p", "no:cacheprovider", "--no-header"],
        cwd=str(CHECKOUT_ROOT),
        env=_child_env(),
        capture_output=True,
        text=True,
        timeout=300,
    )
    out = result.stdout + result.stderr

    assert result.returncode == 0, out
    assert "1 passed" in out, out
    assert "wrong `ragstack` import origin" not in out, f"the guard misfired:\n{out}"


# --------------------------------------------------------------------------
# 2. the Elasticsearch opt-in
# --------------------------------------------------------------------------

_ES_TEST = "tests/integration/test_elasticsearch.py"


#: pytest exits 5 when every collected test skipped ("no tests ran"), 0 when at
#: least one ran. Both are "the suite did not blow up"; neither is a pass.
_OK_EXITS = (0, 5)


def _run_es_module(**env_overrides: str | None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", _ES_TEST, "-rs", "-q",
         "-p", "no:cacheprovider", "--no-header"],
        cwd=str(CHECKOUT_ROOT),
        env=_child_env(**env_overrides),
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_es_integration_skips_when_not_opted_in(tmp_path):
    """No opt-in, no cluster. The old default was the production Elasticsearch."""
    result = _run_es_module()
    out = result.stdout + result.stderr

    assert result.returncode in _OK_EXITS, out
    assert "skipped" in out, f"the ES module did not skip:\n{out}"
    assert "RAGSTACK_TEST_ES_URL" in out, f"the skip reason does not name the opt-in:\n{out}"
    assert "SCRATCH" in out, f"the skip reason does not say SCRATCH:\n{out}"
    # It must be the OPT-IN skip, not the reachability skip: "it skipped" alone
    # is also true of a module that took a URL from somewhere and found nothing
    # listening, which is the quiet failure D2 rejected.
    assert "not reachable" not in out, f"the module got a URL from somewhere:\n{out}"
    assert " passed" not in out, f"an ES test ran without an opt-in:\n{out}"


def test_a_stale_old_name_export_does_not_re_arm_the_live_run():
    """**The reason the variable was renamed.**

    Anyone who exported ``TEST_ES_URL=http://localhost:9200`` before this fix —
    or copied it from the pre-#432 CI job — has a shell that would have pointed
    the suite straight back at the production cluster. Under the new name that
    export is inert. Note the value here is only ever *read*: the module skips
    before it constructs a client, so this test never contacts :9200.
    """
    result = _run_es_module(TEST_ES_URL="http://localhost:9200")
    out = result.stdout + result.stderr

    assert result.returncode in _OK_EXITS, out
    assert "skipped" in out, f"a stale TEST_ES_URL re-armed the live run:\n{out}"
    # The opt-in skip specifically. A module that *honoured* the old name would
    # also "skip" whenever the stale value happened to be unreachable — so
    # matching on the reachability message would make this test satisfiable by
    # the very mutant it exists to catch.
    assert "SCRATCH" in out, f"the skip was not the opt-in skip:\n{out}"
    assert "not reachable" not in out, f"TEST_ES_URL became the URL:\n{out}"
    assert " passed" not in out, f"a stale TEST_ES_URL re-armed the live run:\n{out}"


def test_the_opt_in_actually_opts_in(tmp_path):
    """The control for the two skips above: set the variable and the module is
    collected and *runs* — reaching ``_reachable()``, the second gate, which then
    skips against a dead port with a different, honest message.

    Without this, "it always skips" would satisfy every other ES assertion here.
    """
    result = _run_es_module(RAGSTACK_TEST_ES_URL="http://127.0.0.1:1")
    out = result.stdout + result.stderr

    assert result.returncode in _OK_EXITS, out
    if "could not import" in out:
        pytest.skip("the elasticsearch client is not installed in this environment")
    assert "not reachable" in out, (
        f"the module did not get past the opt-in to the reachability gate:\n{out}"
    )
    assert "127.0.0.1:1" in out, f"the reachability skip does not name the URL:\n{out}"


# --------------------------------------------------------------------------
# 3. the dead-port environment for child processes
# --------------------------------------------------------------------------

_SETTINGS_PROBE = """
import json
from ragstack.config import Settings

live = {}
for name, value in Settings().model_dump().items():
    if not isinstance(value, str) or "://" not in value:
        continue
    if "127.0.0.1:1/" in value or value.endswith("127.0.0.1:1"):
        continue          # pinned dead
    if "localhost" in value or "127.0.0.1" in value or "0.0.0.0" in value:
        live[name] = value
print(json.dumps(live))
"""


def test_pinned_env_leaves_no_live_local_default(tmp_path):
    """``PINNED_ENV`` must cover **every** setting that defaults to a local port.

    Asserting the dict's contents would be circular — it would pass whatever the
    dict happens to say. This instead builds real ``Settings`` in a child with
    only ``PINNED_ENV`` applied and asserts nothing still points at a local
    address, so adding a backend to ``config.py`` with a ``localhost`` default
    and forgetting this file is a *failing test* rather than the next incident.

    It has already earned its keep: it caught ``postgres_dsn``, missing from the
    per-incident dict this module was promoted from, and defaulting to exactly
    the DSN that migrated a production ``jobs`` table in #369.
    """
    from tests.pinned_env_support import PINNED_ENV

    probe = tmp_path / "settings_probe.py"
    probe.write_text(_SETTINGS_PROBE)

    result = subprocess.run(
        [sys.executable, str(probe)],
        cwd=str(tmp_path),          # away from any .env in the checkout
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "PYTHONPATH": str(CHECKOUT_ROOT),
            **PINNED_ENV,
        },
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    import json

    live = json.loads(result.stdout)
    assert live == {}, (
        "these settings still resolve to a local address in a child process — on "
        "this host that means a live service. Add each to PINNED_ENV in "
        f"tests/pinned_env_support.py: {live}"
    )
