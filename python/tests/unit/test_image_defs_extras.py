"""The extras each image def installs (#815).

Both ``apptainer/ragstack-tools.def`` and ``apptainer/ragstack-server.def``
install ragstack with ``pip install "/opt/ragstack/python[<extras>]"``. An extra
that drops out of that list is not an install error — pip builds the image, the
import that needs it fails or, worse, silently degrades at runtime. That is how
every image before v1.6.15 shipped without ``nltk``: ``sentence_spans()`` ran
the regex fallback in production while the study, the goldens and dev ran
Punkt. A misspelt extra is no better: pip only *warns* that the package "does
not provide the extra" and installs without it.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DEFS = {
    "tools": REPO / "apptainer" / "ragstack-tools.def",
    "server": REPO / "apptainer" / "ragstack-server.def",
}
PYPROJECT = REPO / "python" / "pyproject.toml"
_INSTALL = re.compile(r'pip install "/opt/ragstack/python\[([^\]]*)\]"')

#: Extras an image must carry. ``chunking`` is nltk — the Punkt segmenter the
#: sentence/semantic chunkers were measured with (#815).
REQUIRED = {"vector", "pdf", "text", "tokenization", "graph", "postgres", "chunking"}


def _extras(def_path: Path) -> list[str]:
    lines = [ln for ln in def_path.read_text().splitlines()
             if not ln.lstrip().startswith("#")]
    found = [m.group(1) for ln in lines if (m := _INSTALL.search(ln))]
    assert len(found) == 1, f"{def_path.name}: expected one ragstack pip install, got {found}"
    return [e.strip() for e in found[0].split(",")]


@pytest.mark.parametrize("kind", sorted(DEFS))
def test_def_installs_the_required_extras(kind):
    extras = _extras(DEFS[kind])
    assert "chunking" in extras, f"{kind} image would run the regex sentence fallback (#815)"
    missing = REQUIRED - set(extras)
    assert not missing, f"{kind} def drops {sorted(missing)}"


@pytest.mark.parametrize("kind", sorted(DEFS))
def test_every_listed_extra_exists_in_pyproject(kind):
    declared = set(tomllib.loads(PYPROJECT.read_text())["project"]["optional-dependencies"])
    extras = _extras(DEFS[kind])
    assert len(extras) == len(set(extras)), f"{kind}: duplicate extra in {extras}"
    unknown = [e for e in extras if e not in declared]
    assert not unknown, f"{kind} def names extras pyproject does not declare: {unknown}"


def test_the_two_images_install_the_same_extras():
    assert _extras(DEFS["tools"]) == _extras(DEFS["server"])
