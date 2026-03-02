"""Utilities for comparing responses across implementations."""

from __future__ import annotations

from typing import Sequence


def assert_sources_overlap(
    sources_a: Sequence[dict],
    sources_b: Sequence[dict],
    min_overlap_ratio: float = 0.6,
) -> None:
    """Assert that two source lists share at least *min_overlap_ratio*
    of their ``chunk_id`` values.

    Parameters
    ----------
    sources_a:
        First list of source dicts (each must contain ``"chunk_id"``).
    sources_b:
        Second list of source dicts.
    min_overlap_ratio:
        Minimum ratio of overlapping chunk IDs relative to the size of the
        smaller set.  Defaults to ``0.6`` (60 %).

    Raises
    ------
    AssertionError
        When the overlap ratio is below the threshold.
    """
    ids_a = {s["chunk_id"] for s in sources_a}
    ids_b = {s["chunk_id"] for s in sources_b}

    if not ids_a and not ids_b:
        return  # both empty -- trivially overlapping

    overlap = ids_a & ids_b
    denominator = min(len(ids_a), len(ids_b)) or 1
    ratio = len(overlap) / denominator

    assert ratio >= min_overlap_ratio, (
        f"Source overlap ratio {ratio:.2f} is below the required "
        f"minimum of {min_overlap_ratio:.2f}.  "
        f"IDs only in A: {ids_a - ids_b}, only in B: {ids_b - ids_a}"
    )


def assert_scores_within_tolerance(
    scores_a: Sequence[float],
    scores_b: Sequence[float],
    rtol: float = 0.1,
) -> None:
    """Assert that two score sequences are element-wise within relative
    tolerance *rtol*.

    Both sequences must have the same length.

    Parameters
    ----------
    scores_a:
        First list of numeric scores.
    scores_b:
        Second list of numeric scores.
    rtol:
        Maximum allowed relative difference for each pair.  Defaults to
        ``0.1`` (10 %).

    Raises
    ------
    AssertionError
        When any pair of scores exceeds the tolerance.
    """
    assert len(scores_a) == len(scores_b), (
        f"Score lists differ in length: {len(scores_a)} vs {len(scores_b)}"
    )

    for idx, (sa, sb) in enumerate(zip(scores_a, scores_b)):
        denom = max(abs(sa), abs(sb)) or 1.0
        rel_diff = abs(sa - sb) / denom
        assert rel_diff <= rtol, (
            f"Score at index {idx} differs by {rel_diff:.4f} "
            f"(a={sa}, b={sb}), exceeds rtol={rtol}"
        )
