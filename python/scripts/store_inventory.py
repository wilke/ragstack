#!/usr/bin/env python
"""Report-only inventory of physical stores vs. the registries that claim them.

**Never deletes anything.** See :mod:`ragstack.ops.store_inventory` for why the
output says ``unclaimed-by-known-registries`` and not ``orphan``.

Reconcile against every deployment sharing the backends — a registry you leave
out is a set of live stores the report calls unclaimed:

    cd python
    python scripts/store_inventory.py \\
        --config-dir /rag/config \\
        --tenants-dir /rag/data/tenants

    # machine-readable, e.g. to diff two runs and watch the gap close
    python scripts/store_inventory.py --tenants-dir /rag/data/tenants --json

    # Qdrant exposes no on-disk size through its API; point at its storage dir
    python scripts/store_inventory.py --config-dir /rag/config \\
        --qdrant-storage http://localhost:6333=/rag/data/qdrant/storage

A single deployment can be named directly, for the common "what does this tenant
actually own?" question:

    python scripts/store_inventory.py --env dev=/rag/data/tenants/dev/config/tenant.env
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ragstack.ops.store_inventory import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
