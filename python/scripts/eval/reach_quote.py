#!/usr/bin/env python3
"""quote@10 for the reach comparison (``docs/plans/results/reach-rechunk-2026-11/PREREG.md`` §3).

One function, :func:`quote_cover`, scores both sides (W: fixed 512-token windows; S: the adopted
chunking). It needs only the quote and the texts of the retrieved chunks: no document text, no
offsets, so it is symmetric between chunkings by construction. Standard library only.

**Normalisation.** :func:`norm_ws` replaces every maximal run of whitespace (``str.split``'s
definition, which includes Unicode whitespace such as U+00A0 and U+2029) with one ASCII space and
strips both ends. Nothing else changes: case, punctuation and Unicode forms are compared as they are.

**Coverage.** Let q be the normalised quote (length n) and t_1..t_k the normalised texts. A
position p of q (0 <= p < n) is *covered* when any of these holds for some text t:

1. **interior match** — p lies inside a substring q[i:j] that also occurs in t, with
   j - i >= L, where L = min(``min_match``, n) (``min_match`` = 20 by default). The set of such
   positions is the union, over every start i, of q[i : i + m_i] where m_i is the length of the
   longest prefix of q[i:] occurring in t, kept when m_i >= L. (Every position of a common
   substring of length >= L lies in the longest match from that substring's start, so the union is
   exact.) L = n for a quote shorter than ``min_match`` means such a quote is covered only by a
   text that contains all of it (or by rule 2 pieces).
2. **boundary match** — p lies in a prefix q[:j] of the quote that is a suffix of t (t ends with
   q[:j]), or in a suffix q[i:] that is a prefix of t (t starts with q[i:]); any length >= 1. This is
   the split-quote case: when a chunk boundary falls inside the quote, the piece on either side may
   be shorter than L, and these pieces are anchored at the text's edge, so they are not chance
   matches in the middle of an unrelated text.
3. **bridging space** — p is a space whose neighbours p-1 and p+1 are both covered. Chunk texts
   are stored stripped, so the space at a chunk boundary inside a quote belongs to neither chunk.

``quote_cover`` returns (number of covered positions) / n, in [0, 1]; an empty normalised quote
returns 0.0. The **hit rule** is coverage >= :data:`HIT_THRESHOLD` (0.90): :func:`quote_hit`.

What this does not do: it does not check that the pieces come from the gold location or are in
order (a 20-character run repeated elsewhere in the corpus counts), and it does not tolerate
character-level edits (a quote that differs from the source by a single character in every
20-character window is not covered). The prereg excludes quotes that are not verbatim substrings
of their gold chunk for this reason (§2, §11 Q4).
"""
from __future__ import annotations

from collections.abc import Iterable

MIN_MATCH = 20
HIT_THRESHOLD = 0.90


def norm_ws(s: str) -> str:
    """Collapse every whitespace run to one space and strip the ends."""
    return " ".join(s.split())


def _interior(q: str, t: str, lmin: int, mask: list[bool]) -> None:
    """Mark every position of q inside a common substring of q and t of length >= lmin."""
    n = len(q)
    # Fast exit (exact, not a heuristic): any substring of q of length >= lmin contains one of the
    # aligned blocks q[k*b : k*b + b] with b = (lmin + 1) // 2, so no aligned block in t means no
    # interior match.
    b = (lmin + 1) // 2
    if not any(q[k:k + b] in t for k in range(0, n - b + 1, b)):
        return
    j = 0  # end of the longest match from the current start; never decreases (q[i:j] in t => q[i+1:j] in t)
    marked = 0  # positions < marked are already set
    for i in range(n - lmin + 1):
        if j < i + lmin:
            j = i + lmin
            if q[i:j] not in t:
                continue
        while j < n and q[i:j + 1] in t:
            j += 1
        # q[i:j] occurs in t and is the longest such prefix of q[i:]
        for p in range(max(i, marked), j):
            mask[p] = True
        marked = max(marked, j)


def _boundary(q: str, t: str, mask: list[bool]) -> None:
    """Mark the longest quote prefix that ends t and the longest quote suffix that starts t."""
    n = len(q)
    top = min(n, len(t))
    last, first = t[-1], t[0]
    for j in range(top, 0, -1):
        if q[j - 1] == last and t.endswith(q[:j]):
            for p in range(j):
                mask[p] = True
            break
    for m in range(top, 0, -1):
        if q[n - m] == first and t.startswith(q[n - m:]):
            for p in range(n - m, n):
                mask[p] = True
            break


def cover_mask(quote: str, texts: Iterable[str], min_match: int = MIN_MATCH) -> tuple[str, list[bool]]:
    """``(normalised quote, per-position covered flags)`` under the three rules above."""
    if min_match < 1:
        raise ValueError("min_match must be >= 1")
    q = norm_ws(quote)
    n = len(q)
    mask = [False] * n
    if n == 0:
        return q, mask
    lmin = min(min_match, n)
    for raw in texts:
        t = norm_ws(raw)
        if not t:
            continue
        _interior(q, t, lmin, mask)
        _boundary(q, t, mask)
    for p in range(1, n - 1):
        if q[p] == " " and not mask[p] and mask[p - 1] and mask[p + 1]:
            mask[p] = True
    return q, mask


def quote_cover(quote: str, texts: Iterable[str], min_match: int = MIN_MATCH) -> float:
    """Fraction of the whitespace-normalised quote's characters covered by the texts (module doc)."""
    q, mask = cover_mask(quote, texts, min_match)
    return sum(mask) / len(q) if q else 0.0


def quote_hit(quote: str, texts: Iterable[str], threshold: float = HIT_THRESHOLD,
              min_match: int = MIN_MATCH) -> bool:
    """The prereg's quote@k rule: coverage of the quote by the top-k texts >= 0.90."""
    return quote_cover(quote, texts, min_match) >= threshold


def is_verbatim(quote: str, text: str) -> bool:
    """The §2 eligibility test: the normalised quote is a substring of the normalised text."""
    q = norm_ws(quote)
    return bool(q) and q in norm_ws(text)
