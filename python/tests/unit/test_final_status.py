"""Unit tests for the batch-ingest final-status decision."""
from ragstack.api.routers.documents import _final_status
from ragstack.jobstore import COMPLETED, FAILED, PENDING


def _counts(completed=0, failed=0, pending=0):
    return {COMPLETED: completed, FAILED: failed, PENDING: pending}


def test_all_completed_is_completed():
    assert _final_status(_counts(completed=3)) == COMPLETED


def test_partial_failure_stays_completed():
    # At least one item landed — surface the failures via items.failed, not status.
    assert _final_status(_counts(completed=2, failed=1)) == COMPLETED


def test_every_item_failed_is_failed():
    assert _final_status(_counts(failed=3)) == FAILED


def test_no_items_is_completed():
    # Empty directory: nothing to do is not a failure.
    assert _final_status(_counts()) == COMPLETED


def test_nothing_completed_with_leftover_pending_is_failed():
    # A shard that raised wholesale reports its items failed but never checkpoints
    # them, so they linger pending; the run must not read as completed.
    assert _final_status(_counts(pending=4)) == FAILED
    assert _final_status(_counts(failed=1, pending=3)) == FAILED
