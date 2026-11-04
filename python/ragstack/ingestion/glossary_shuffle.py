"""The shuffled-glossary control: permute RENDERED ``symbol_glossary`` fields
among the chunks of one index that carry a non-empty one.

The question the control answers is whether *the right* glossary matters, or
merely *a* glossary: the shuffled index has exactly the same chunks with a
field, and exactly the same multiset of field strings (so the same length
distribution and the same vocabulary in the glossary leg), but each string sits
on a chunk it was not rendered for. Chunks with an empty field stay empty.

Deterministic and version-proof: the chunk keys are sorted, and the
permutation is drawn by a Fisher-Yates shuffle driven by a SHA-256 counter
stream of the seed (not :mod:`random`, whose algorithms are not a durable
contract). The same seed and the same ``{key: field}`` mapping always give the
same assignment, on any Python.

**Fixed points.** A chunk "keeps its own string" when the string it receives is
EQUAL to the one it had — by value, not by position: two chunks of one document
often render the same line, and swapping them changes nothing. After the
shuffle, every such chunk is repaired by a swap with another position that
leaves NEITHER position holding its own string, searched in a seeded order;
passes repeat until nothing changes. What remains is unavoidable for this
greedy repair (e.g. one string held by more than half of the populated chunks,
or a single populated chunk) and is reported as ``fixed_points`` — never
silently dropped.

Recorded with every use (:meth:`ShuffleResult.record`): the seed, the number of
populated chunks, the residual fixed points, and ``permutation_sha256`` — the
sha256 of the (target key, source key) pairs, which pins the exact assignment.
A store write stamps each chunk ``<version>@file/1`` + :meth:`ShuffleResult.stamp_suffix`
(``+shuf/1:<seed>:<first 12 hex of the permutation hash>``), so a census of the
version stamps tells a shuffled index from the true one.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: Bump with any change to the algorithm (the stream, the shuffle, the repair):
#: the same seed must keep meaning the same permutation.
SHUFFLE_SCHEME = "shuf/1"

#: A seed is free text with a restricted alphabet: it is written into the
#: version stamp, where ``:`` and ``@`` are separators.
_SEED = re.compile(r"[A-Za-z0-9|._+-]{1,64}")


class ShuffleError(ValueError):
    """A bad seed, or an index that changed between the planning pass and the write."""


def check_seed(seed: str) -> str:
    if not isinstance(seed, str) or _SEED.fullmatch(seed) is None:
        raise ShuffleError(
            f"shuffle seed {seed!r}: 1-64 characters of letters, digits and '|._+-' "
            "(it is written into the version stamp)")
    return seed


class _Stream:
    """Uniform integers from SHA-256(scheme | seed | counter)."""

    def __init__(self, seed: str) -> None:
        self._key = f"{SHUFFLE_SCHEME}|{seed}|".encode()
        self._counter = 0
        self._buf = b""

    def _u64(self) -> int:
        if len(self._buf) < 8:
            self._buf += hashlib.sha256(self._key + str(self._counter).encode()).digest()
            self._counter += 1
        out, self._buf = self._buf[:8], self._buf[8:]
        return int.from_bytes(out, "big")

    def below(self, n: int) -> int:
        """Uniform in ``[0, n)`` by rejection (no modulo bias)."""
        if n <= 0:
            raise ValueError("n must be positive")
        limit = (1 << 64) - ((1 << 64) % n)
        while True:
            x = self._u64()
            if x < limit:
                return x % n


@dataclass(frozen=True)
class ShuffleResult:
    seed: str
    #: key -> the string the chunk carried before (populated chunks only).
    original: dict[str, str]
    #: key -> the string it carries after (populated chunks only).
    assignment: dict[str, str]
    #: key -> the key whose string it now carries.
    source: dict[str, str]
    #: populated chunks still holding a string equal to their own (unavoidable).
    fixed_points: int
    #: the same count before the repair (diagnostic).
    fixed_points_before_repair: int
    permutation_sha256: str
    #: keys given with an empty field (left empty).
    empty: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def populated(self) -> int:
        return len(self.original)

    def rendered(self, key: str, true_field: str) -> str:
        """The field ``key`` gets under the shuffle, given the field it renders
        to truly. Raises :class:`ShuffleError` when ``true_field`` is not what
        the shuffle was computed from (the index changed between passes)."""
        if (self.original.get(key, "")) != (true_field or ""):
            raise ShuffleError(
                f"chunk {key!r}: its rendered field differs from the one the shuffle was "
                "planned on (the index or the file changed between passes); nothing more "
                "is written")
        return self.assignment.get(key, "")

    def stamp_suffix(self) -> str:
        return f"+{SHUFFLE_SCHEME}:{self.seed}:{self.permutation_sha256[:12]}"

    def record(self) -> dict[str, Any]:
        """What results and provenance record about the shuffle."""
        return {
            "scheme": SHUFFLE_SCHEME,
            "seed": self.seed,
            "populated": self.populated,
            "empty": self.empty,
            "moved": sum(1 for k in self.source if self.source[k] != k),
            "fixed_points": self.fixed_points,
            "fixed_points_before_repair": self.fixed_points_before_repair,
            "fixed_point_rule": ("a chunk is a fixed point when the string it receives EQUALS its "
                                 "own (value, not position); repaired by seeded swaps; the residual "
                                 "is unavoidable for the greedy repair"),
            "permutation_sha256": self.permutation_sha256,
            "multiset_sha256": multiset_sha256(self.assignment.values()),
            "stamp_suffix": self.stamp_suffix(),
        }


def multiset_sha256(strings: Any) -> str:
    """Order-free hash of a multiset of strings (equal for the true and the shuffled index)."""
    return hashlib.sha256(json.dumps(sorted(strings), ensure_ascii=False).encode()).hexdigest()


def shuffle_rendered(fields: Mapping[str, str], seed: str) -> ShuffleResult:
    """Permute the non-empty values of ``fields`` (chunk key -> rendered field)
    among the keys that hold one. See the module docstring for the rule."""
    check_seed(seed)
    keys = sorted(k for k, v in fields.items() if v)
    empty = sum(1 for v in fields.values() if not v)
    strings = [fields[k] for k in keys]
    n = len(keys)
    rng = _Stream(seed)
    perm = list(range(n))
    for i in range(n - 1, 0, -1):  # Fisher-Yates
        j = rng.below(i + 1)
        perm[i], perm[j] = perm[j], perm[i]

    def fixed(i: int) -> bool:
        return strings[perm[i]] == strings[i]

    before = sum(1 for i in range(n) if fixed(i))
    changed = True
    while changed and n > 1:
        changed = False
        for i in range(n):
            if not fixed(i):
                continue
            start = rng.below(n)
            for step in range(n):
                j = (start + step) % n
                if j == i:
                    continue
                # after the swap: i gets strings[perm[j]], j gets strings[perm[i]]
                if strings[perm[j]] != strings[i] and strings[perm[i]] != strings[j]:
                    perm[i], perm[j] = perm[j], perm[i]
                    changed = True
                    break
    assert Counter(perm) == Counter(range(n))
    source = {keys[i]: keys[perm[i]] for i in range(n)}
    assignment = {keys[i]: strings[perm[i]] for i in range(n)}
    pairs = [[keys[i], keys[perm[i]]] for i in range(n)]
    digest = hashlib.sha256(json.dumps({"scheme": SHUFFLE_SCHEME, "pairs": pairs},
                                       ensure_ascii=False).encode()).hexdigest()
    return ShuffleResult(seed=seed, original={k: fields[k] for k in keys}, assignment=assignment,
                         source=source, fixed_points=sum(1 for i in range(n) if fixed(i)),
                         fixed_points_before_repair=before, permutation_sha256=digest, empty=empty)
