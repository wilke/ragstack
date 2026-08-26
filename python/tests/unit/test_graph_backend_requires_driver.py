"""Regression test for #404: a missing ``neo4j`` driver must fail with an
actionable message, not a bare ``ModuleNotFoundError``.

Before this fix, ``ragstack.api.deps._build_graph_store`` only guarded the
*module* import of ``ragstack.stores.neo4j`` (which always succeeds — the
driver import is lazy, inside ``Neo4jGraphStore.__init__``). Constructing the
store when the driver package isn't installed therefore raised a bare
``ModuleNotFoundError`` at API startup, naming neither the cause nor the fix
(deps.py:929 -> stores/neo4j.py:108 in the issue's trace).

This test never touches a real driver either way: it simulates the driver's
*absence* by putting ``None`` in ``sys.modules['neo4j']`` (the standard way to
force ``import neo4j`` to raise ImportError, regardless of whether the
package happens to be installed in the environment running the suite) and
asserts ``Neo4jGraphStore(...)`` raises a ``RuntimeError`` naming the
``graph`` extra rather than letting the ``ModuleNotFoundError`` escape bare.
"""
from __future__ import annotations

import sys

import pytest

from ragstack.stores.neo4j import Neo4jGraphStore


def test_missing_driver_raises_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # `None` in sys.modules is what makes `import neo4j` (and `from neo4j
    # import ...`) raise ImportError even when the package is actually
    # installed in this environment.
    monkeypatch.setitem(sys.modules, "neo4j", None)

    with pytest.raises(RuntimeError) as exc_info:
        Neo4jGraphStore(uri="bolt://x:7687", user="neo4j", password="ragstack")

    message = str(exc_info.value)
    # Must name the actual fix, not just say "something is missing" — an
    # operator reading a startup traceback needs to know which extra to
    # install without having to go read the source.
    assert "graph" in message
    assert "neo4j" in message
    assert "install" in message.lower()
    # The ModuleNotFoundError must not have escaped bare: it's chained as the
    # cause of the RuntimeError, not silently swallowed.
    assert isinstance(exc_info.value.__cause__, ImportError)


def test_missing_driver_is_not_a_bare_module_not_found_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-vacuity check for the test above: confirm ImportError alone (the
    pre-fix behaviour) is a *different, less specific* exception type than
    what the store now raises, so a regression that reintroduces the bare
    ``from neo4j import ...`` (no try/except) would fail this suite by
    raising ModuleNotFoundError instead of RuntimeError."""
    monkeypatch.setitem(sys.modules, "neo4j", None)

    with pytest.raises(RuntimeError):
        try:
            Neo4jGraphStore(uri="bolt://x:7687", user="neo4j", password="ragstack")
        except ModuleNotFoundError:
            pytest.fail(
                "Neo4jGraphStore() let a bare ModuleNotFoundError escape "
                "instead of wrapping it in an actionable RuntimeError (#404)"
            )
