"""#849: a workflow carries no policy of its own.

Every script a CWL runs acts on its inputs and on the registry entry it
resolves — never on a deployment default reaching it through the worker's
environment. The two quotas that did (the chunk cap, the graph budget) are
inputs now; this pins that no script a CWL runs — nor any ``ragstack`` module
it imports, transitively — reads ``settings.max_chunks_per_collection`` or
``settings.graph_max_triples_per_collection``.

Static (AST) on purpose: importing the scripts would build the settings
object, and a test that only exercises one code path cannot see a read on
another.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PY = REPO / "python"
CWL_DIR = REPO / "cwl"

#: The policy settings no workflow-run code may read (#849).
POLICY_SETTINGS = ("max_chunks_per_collection", "graph_max_triples_per_collection")


def _cwl_scripts() -> list[Path]:
    names: set[str] = set()
    for cwl in sorted(CWL_DIR.glob("*.cwl")):
        names.update(re.findall(r"scripts/([A-Za-z0-9_]+\.py)", cwl.read_text(encoding="utf-8")))
    return [PY / "scripts" / n for n in sorted(names)]


def _module_path(name: str) -> Path | None:
    base = PY / Path(*name.split("."))
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    return None


def _imports(path: Path) -> set[str]:
    """``ragstack`` modules ``path`` imports, anywhere in the file (a
    function-level import counts: it runs on some path)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pkg = ".".join(path.relative_to(PY).with_suffix("").parts[:-1])
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names if a.name.split(".")[0] == "ragstack")
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = pkg.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
                mod = f"{base}.{node.module}" if node.module else base
            else:
                mod = node.module or ""
            if mod.split(".")[0] != "ragstack":
                continue
            out.add(mod)
            # `from ragstack.ingestion import shard` imports a submodule
            out.update(f"{mod}.{a.name}" for a in node.names)
    return out


def _closure(script: Path) -> dict[str, Path]:
    seen: dict[str, Path] = {}
    todo = list(_imports(script))
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        path = _module_path(name)
        if path is None:
            continue
        seen[name] = path
        # a package's __init__ runs on import of any submodule
        parts = name.split(".")
        todo.extend(".".join(parts[:i]) for i in range(1, len(parts)))
        todo.extend(_imports(path))
    return seen


def _reads(path: Path) -> list[tuple[int, str]]:
    """Attribute reads of a policy setting (``x.max_chunks_per_collection``),
    and ``getattr(x, "<setting>")``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in POLICY_SETTINGS:
            out.append((node.lineno, node.attr))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "getattr" and len(node.args) >= 2
              and isinstance(node.args[1], ast.Constant) and node.args[1].value in POLICY_SETTINGS):
            out.append((node.lineno, str(node.args[1].value)))
    return out


def test_the_cwls_run_scripts():
    scripts = _cwl_scripts()
    assert scripts, "no script found in cwl/*.cwl — the walker is broken"
    assert all(s.is_file() for s in scripts), [str(s) for s in scripts if not s.is_file()]
    assert PY / "scripts" / "load_embeddings.py" in scripts


@pytest.mark.parametrize("script", _cwl_scripts(), ids=lambda p: p.name)
def test_no_workflow_run_script_reads_a_policy_setting(script: Path):
    offenders = [f"{script.relative_to(REPO)}:{line}: .{attr}" for line, attr in _reads(script)]
    for name, path in sorted(_closure(script).items()):
        offenders += [f"{path.relative_to(REPO)}:{line}: .{attr} (via {name})"
                      for line, attr in _reads(path)]
    assert not offenders, (
        "a script a CWL runs reads a deployment policy setting (#849) — make it a "
        "workflow input with a 0/off default instead:\n" + "\n".join(offenders))


def test_the_walker_sees_a_read():
    """The pin is not vacuous: the API module that DOES derive the cap is
    caught by the same reader."""
    api = PY / "ragstack" / "api" / "routers" / "documents.py"
    assert any(attr == "max_chunks_per_collection" for _, attr in _reads(api))
