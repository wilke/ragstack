"""Perf budget for ``AclStore.count_owned`` (issue #290 / #355 convention):
p95 < 1 ms on sqlite with 10k share rows.

The query is one indexed COUNT(*) over ``shares_active_owned_by`` (grantee_id,
permission) WHERE revoked_at = '' — the point of adding that index rather than
relying on the existing collection_id-first ones. Rows are inserted directly
through the connection (bulk, uncommitted-per-row) rather than via 10k
``grant()`` calls, since building the fixture is not what is timed.
"""
from __future__ import annotations

from contextlib import closing

import pytest

from ragstack.acl_store import PERM_OWNER, PERM_READ, SqliteAclStore, _now
from tests.perf._budget import assert_budget_async

_N_ROWS = 10_000
_TARGET = "bvbrc:target@patricbrc.org"


def _seed(store: SqliteAclStore, n: int) -> None:
    with closing(store._connect()) as conn, conn:
        for i in range(n):
            # Every 7th row is the subject under test owning a distinct
            # collection; the rest are noise from other subjects/permissions —
            # the index must not degrade into a table scan as the table grows.
            if i % 7 == 0:
                grantee, perm = _TARGET, PERM_OWNER
            else:
                grantee, perm = f"bvbrc:other-{i}@patricbrc.org", PERM_READ
            conn.execute(
                "INSERT INTO shares (id, collection_id, grantee_type, grantee_id, "
                "permission, granted_by, granted_at, revoked_at) VALUES "
                "(?, ?, 'user', ?, ?, ?, ?, '')",
                (f"share-{i}", f"col-{i}", grantee, perm, grantee, _now()),
            )


@pytest.mark.perf
@pytest.mark.asyncio
async def test_count_owned_p95_budget_10k_rows(tmp_path):
    store = SqliteAclStore(str(tmp_path / "perf.db"))
    _seed(store, _N_ROWS)
    assert await store.count_owned(_TARGET) == len(range(0, _N_ROWS, 7))

    await assert_budget_async(
        "count_owned_10k_rows", lambda: store.count_owned(_TARGET), budget_s=0.001, n=50,
    )
    await store.close()
