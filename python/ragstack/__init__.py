"""ragstack — the Python implementation.

``ragstack.__version__`` is the repo version as PEP 440 (``1.6.4`` on a
release tag, ``1.6.4+a2be96f`` past one), resolved lazily on first access by
:func:`ragstack.version.package_version` — a git checkout, else the generated
``ragstack/_release.py`` the tools-image build writes, else the distribution
version. See :mod:`ragstack.version` for the resolution order and why the
``+<sha>`` segment never orders.
"""
from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    # Lazy so importing the package never runs git; the value is cached by
    # ragstack.version for the life of the process once asked for.
    if name == "__version__":
        from ragstack.version import package_version

        return package_version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
