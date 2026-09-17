#!/usr/bin/env python
"""Report where a live collection's Elasticsearch mapping diverges from the
declared chunk-metadata schema (``contracts/schemas/chunk_metadata.json``).

**Read-only** — the only requests it makes are ``GET /_cat/indices`` and
``GET /<index>/_mapping``. **Report-only** — it exits 0 on divergence, because an
ES mapping cannot be changed in place and every finding is therefore a reindex,
not a fix. See :mod:`ragstack.ops.metadata_conformance` for what each verdict
means and, in particular, why ``absent`` and ``undeclared`` are not defects.

    cd python
    python scripts/metadata_conformance.py --es-url http://127.0.0.1:9200

    # just this deployment's collections, with the fields no document has
    # materialized yet listed too
    python scripts/metadata_conformance.py --es-url http://127.0.0.1:24083 \\
        --index 'ragstack_lib_*' -v

    # machine-readable, e.g. to diff two runs and watch a migration land
    python scripts/metadata_conformance.py --es-url http://127.0.0.1:9200 --json

``--fail-on-divergence`` turns it into a gate, for a tenant whose collections are
already clean and who wants to keep them that way. Do not put it in CI against
the shared clusters until the fleet has been migrated.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ragstack.ops.metadata_conformance import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
