"""The one place that says which chunk methods the out-of-process ingest can do.

#609: the API minted a `semantic` collection and the GoWe scatter's shard step
refused every one of its 20 documents, three attempts running, because the tool
and the API each decided for themselves what "supported" meant. These tests pin
the properties that make ONE constant load-bearing:

* the tool's refusal and the API's refusal are the same string, so they cannot
  drift into disagreeing about the same collection;
* the supported list is DERIVED from ``CHUNK_METHODS`` by subtraction, so adding
  a method to the chunker registry cannot leave it unadvertised here;
* the set is a set, not a prefix test — ``startswith("semantic")`` was the old
  check and it would also swallow a future ``semantic_whatever`` the tool CAN do.

There is no test that the refusal string matches a literal sentence: that is the
message, not the contract. What is asserted is that it names the offending method
and every supported one, which is what a caller needs to act on it.
"""
from __future__ import annotations

import pytest

from ragstack.ingestion.chunker_config import (
    SEMANTIC_METHODS,
    SHARD_UNSUPPORTED_METHODS,
    needs_embed_fn,
    parse_unsupported_methods,
    shard_refusal,
    shard_supported_methods,
)
from ragstack.ingestion.chunkers import CHUNK_METHODS


def test_the_default_refuses_the_semantic_methods() -> None:
    """A DEFAULT, not a fact about the library (#609).

    Which methods a worker can run is a property of the deployed image — the API
    and the worker fleet release and roll separately, and this host has two worker
    image directories. So the set is a deployment setting and this constant is
    only its conservative default.
    """
    assert SHARD_UNSUPPORTED_METHODS == set(SEMANTIC_METHODS)


def test_an_empty_setting_admits_everything() -> None:
    """The state after an image carrying the semantic wiring is rolled."""
    for method in CHUNK_METHODS:
        assert shard_refusal(method, unsupported=frozenset()) is None


def test_the_setting_parses_and_rejects_a_typo() -> None:
    assert parse_unsupported_methods("semantic,semantic_pooled") == set(SEMANTIC_METHODS)
    assert parse_unsupported_methods("") == frozenset()
    assert parse_unsupported_methods(None) == frozenset()
    assert parse_unsupported_methods(" semantic , semantic_pooled ") == set(SEMANTIC_METHODS)
    # A typo must fail loudly. Silently guarding nothing while looking exactly
    # like a guard that works is the failure mode this whole issue is about.
    with pytest.raises(ValueError, match="semantik"):
        parse_unsupported_methods("semantik")


def test_semantic_methods_is_the_one_membership_test() -> None:
    """`needs_embed_fn` is what the five hand-written copies became.

    The deferred rename (`semantic` -> `semantic_window`, plan §7d) is a one-line
    change to this tuple only if nothing tests membership by hand again.
    """
    assert set(SEMANTIC_METHODS) <= set(CHUNK_METHODS)
    for m in CHUNK_METHODS:
        assert needs_embed_fn(m) is (m in SEMANTIC_METHODS)
    assert needs_embed_fn(None) is False


def test_every_unsupported_method_is_a_real_chunk_method() -> None:
    # A typo here would silently guard nothing: shard_refusal() would return None
    # for the real method and the API would go back to minting broken collections.
    assert SHARD_UNSUPPORTED_METHODS <= set(CHUNK_METHODS)


def test_supported_is_the_complement_and_stays_in_registry_order() -> None:
    assert shard_supported_methods() == tuple(
        m for m in CHUNK_METHODS if m not in SHARD_UNSUPPORTED_METHODS
    )
    assert set(shard_supported_methods()).isdisjoint(SHARD_UNSUPPORTED_METHODS)
    # The union is total: every method is either offered or refused, never absent
    # from both (which is how a caller gets a refusal naming no alternative).
    assert set(shard_supported_methods()) | SHARD_UNSUPPORTED_METHODS == set(CHUNK_METHODS)


@pytest.mark.parametrize("method", sorted(SHARD_UNSUPPORTED_METHODS))
def test_refusal_names_the_method_and_the_alternatives(method: str) -> None:
    msg = shard_refusal(method)
    assert msg is not None
    assert method in msg
    for ok in shard_supported_methods():
        assert ok in msg, f"refusal does not name supported method {ok!r}"
    assert "609" in msg  # the reader needs somewhere to go


@pytest.mark.parametrize("method", sorted(set(CHUNK_METHODS) - SHARD_UNSUPPORTED_METHODS))
def test_supported_methods_are_not_refused(method: str) -> None:
    assert shard_refusal(method) is None


def test_none_is_not_refused() -> None:
    # An entry with no chunk_method falls back to the server default at the call
    # site; returning a refusal for None would 422 every such collection.
    assert shard_refusal(None) is None


def test_unknown_method_is_not_refused_here() -> None:
    # Not this guard's job — an unknown method is already a 400 from
    # _validate_chunk. Refusing it here too would report the wrong reason.
    assert shard_refusal("no-such-method") is None


def test_it_is_a_set_not_a_semantic_prefix() -> None:
    # The old check was args.chunk_method.startswith("semantic"), which refuses
    # any future method whose name merely begins that way.
    assert shard_refusal("semantic_future_thing_the_tool_can_do") is None
