#!/usr/bin/env python
"""List documents in a collection that are the same work — a shared DOI, PMCID
or PMID (#747). The backlog the ingest-time duplicate guard cannot see into.

**Read-only** — ``GET /_cat/indices`` and ``size=0`` aggregation searches only.
**Report-only** — always exits 0; which copy to keep is the owner's decision
(``DELETE /v1/documents/{id}``). See :mod:`ragstack.ops.duplicates`.

    cd python
    python scripts/duplicate_report.py --es-url http://localhost:24043 \\
        --index 'ragstack_lib_openchia_*'

    # one tenant's documents only, machine-readable
    python scripts/duplicate_report.py --es-url http://localhost:24043 \\
        --index 'ragstack_lib_*' --tenant dev --json
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ragstack.ops.duplicates import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
