"""Guard against the personal contact address creeping back into the tree (#626).

Ten committed files on ``main`` once carried ``awilke1972@gmail.com`` — a
personal address — as the Crossref/NCBI/PubTator "polite pool" contact, in
study scripts, two results write-ups, and the plugin manifests. The project
contact for that purpose is ``wilke@anl.gov``; the library already has a
config variable for it (``DOI_ENRICHMENT_MAILTO``, ``config.py:367``) and the
study scripts were migrated to read it, with no personal default, failing
loudly if unset.

There is no config surface that catches a hand-typed literal creeping back
in, so this test walks the tree looking for the address directly — the same
way the original ten were found (``git grep -n awilke1972``).
"""
from __future__ import annotations

from pathlib import Path

NEEDLE = "awilke1972"

REPO_ROOT = Path(__file__).resolve().parents[3]
SELF = Path(__file__).resolve()

# Directories that are either not part of the shipped/reviewed tree, or whose
# contents are binary/vendored noise a text scan should not open.
EXCLUDED_DIR_NAMES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "venv",
}

# Extensions worth treating as text; anything else (images, archives, model
# weights, etc.) is skipped rather than opened and decoded.
TEXT_SUFFIXES = {
    "",
    ".py",
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".sh",
    ".toml",
    ".cfg",
    ".ini",
    ".env",
    ".example",
    ".go",
    ".mod",
    ".sum",
    ".rst",
}


def _iter_text_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path == SELF:
            # This file names and quotes the needle on purpose, to explain
            # what it guards against.
            continue
        if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
            continue
        if path.suffix not in TEXT_SUFFIXES:
            continue
        yield path


def test_personal_contact_address_is_not_hardcoded_anywhere() -> None:
    """``git grep -n awilke1972`` must return nothing.

    The project contact for Crossref/NCBI/PubTator polite-pool use is
    ``wilke@anl.gov`` (owner decision, 2026-10-04); the personal gmail
    address must never be hardcoded, defaulted, or quoted in the tree.
    """
    hits: list[str] = []
    for path in _iter_text_files(REPO_ROOT):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if NEEDLE not in text:
            continue
        rel = path.relative_to(REPO_ROOT)
        for lineno, line in enumerate(text.splitlines(), start=1):
            if NEEDLE in line:
                hits.append(f"{rel}:{lineno}: {line.strip()}")

    assert not hits, (
        "personal contact address found hardcoded — use DOI_ENRICHMENT_MAILTO "
        "(or the project contact wilke@anl.gov in prose/records) instead:\n"
        + "\n".join(hits)
    )
