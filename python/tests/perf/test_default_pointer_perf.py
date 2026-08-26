"""Perf budget for the `default` pointer (#276): resolving an omitted
``collection`` is ONE dict lookup on the in-process registry — the durable
collection store is never consulted.

(Both still true, and both only about ``CollectionRegistry.resolve``. Since #419
*choosing which id to resolve* for a request that omits ``collection`` costs one
batched ACL round trip, in the routers. This file times the registry in-process
— no HTTP, never through ``_resolve_entry``, and no ``api_keys`` configured, so
``filter_readable`` would no-op anyway. It therefore neither measures nor bounds
that cost, and a green run here is not evidence about it.)

The registry's ``resolve(None)`` / ``resolve("default")`` p95 stays in the
microseconds over a 1,000-entry registry (``assert_budget``). The
no-store-call assertions — a counting fake ``CollectionStore`` that is never
reached — live in ``tests/unit/test_registry_default_pointer.py`` (on the
registry) and ``tests/api/test_default_pointer.py`` (installed on the app and
hit through ``/v1/chunks`` / ``/v1/retrieve`` / ``/v1/query``).
"""
from __future__ import annotations

import pytest

from ragstack.api.collections import CollectionEntry, CollectionRegistry
from tests.perf._budget import assert_budget


def _entry(cid: str, shared: bool = False) -> CollectionEntry:
    return CollectionEntry(
        id=cid, label=cid, collection=cid, model="m", dim=4, chunk_method="fixed",
        chunk_size=None, chunk_overlap=None, chunk_params={}, is_shared_surface=shared,
        retriever=None, vector_store=None, text_index=None,
    )


@pytest.mark.perf
def test_pointer_resolution_over_1000_entries_is_a_dict_lookup():
    entries = [_entry("phys", shared=True)] + [_entry(f"lib-{i}") for i in range(1000)]
    reg = CollectionRegistry(entries, default_id="lib-500")

    def resolve_both() -> None:
        for _ in range(1000):
            assert reg.resolve(None).id == "lib-500"
            assert reg.resolve("default").id == "lib-500"

    # 2,000 resolutions per sample; 5 ms for the lot is ~2.5 µs each — an order
    # of magnitude over a dict lookup even on a loaded box (the perf suite runs
    # the archive packer alongside), while a store round-trip, or even a linear
    # scan of the 1,001 entries, blows it immediately.
    assert_budget("pointer_resolve_x2000", resolve_both, budget_s=0.005, n=50)
