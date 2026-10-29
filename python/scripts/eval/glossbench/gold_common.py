"""Pure helpers shared by the glossary gold-labelling scripts (precision, prereg §6).

No I/O beyond :func:`write_private` / :func:`sha256_file`; everything else is a pure
function of its arguments, so the numbers in a report can be re-derived by hand.

Labels (rubric v3): ``correct``, ``partial``, ``wrong``, ``not-a-symbol``.
"""
from __future__ import annotations

import hashlib
import os
import random
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

LABELS: tuple[str, ...] = ("correct", "partial", "wrong", "not-a-symbol")
MERGE_PARTIAL = {"partial": "correct"}
SPLIT = "split"

ItemKey = tuple[str, str]  # (document key, item id)


def sha256_file(p: str | os.PathLike[str]) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def write_private(path: Path, data: str) -> None:
    """Write ``data`` with mode 0600 (private outputs: document text, labels)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(data)
    os.chmod(path, 0o600)


def norm_def(s: str) -> str:
    """Casefolded, whitespace-collapsed definition (the item key's second half)."""
    return re.sub(r"\s+", " ", s).strip().casefold()


def seeded(seed: str, sep: str, *parts: object) -> random.Random:
    """``random.Random(sep.join([seed, *parts]))``: one stream per purpose, stable
    across Python versions (string seeds hash with SHA-512)."""
    return random.Random(sep.join(str(p) for p in (seed, *parts)))


def doc_alias(key: str) -> str:
    """The corpus alias of a document key ``<alias>-NN`` (the alias may contain ``-``)."""
    return key.rsplit("-", 1)[0]


# --------------------------------------------------------------------------- sampling
def terciles(pop: Sequence[str], size: Mapping[str, int], tie: random.Random) -> list[list[str]]:
    """Rank ``pop`` by ``size`` (ties by a seeded key drawn in ``pop`` order) and cut at
    ``round(n/3)``, ``round(2n/3)``: three groups whose sizes differ by at most 1."""
    keys = {d: (size[d], tie.random()) for d in pop}
    ranked = sorted(pop, key=lambda d: keys[d])
    n = len(ranked)
    cuts = [0, round(n / 3), round(2 * n / 3), n]
    return [ranked[cuts[i]:cuts[i + 1]] for i in range(3)]


def group_items(proposals: Mapping[str, Iterable[tuple[str, str, int]]]) -> dict[tuple[str, str], dict[str, Any]]:
    """``{arm: [(symbol, definition, offset)]}`` -> distinct items keyed by
    ``(symbol, norm_def(definition))`` with the arms, surface spellings and offsets."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for arm, props in proposals.items():
        for sym, text, off in props:
            g = groups.setdefault((sym, norm_def(text)), {"arms": set(), "surface": set(), "offsets": set()})
            g["arms"].add(arm)
            g["surface"].add(text)
            g["offsets"].add(off)
    return groups


# --------------------------------------------------------------------------- labels
def majority(votes: Sequence[str]) -> str | None:
    """Majority label of the judges' votes on one item.

    ``None`` with fewer than two votes (not labelled); the top label when it has at
    least two votes and strictly more than the runner-up; otherwise :data:`SPLIT`
    (excluded and counted, prereg §6)."""
    if len(votes) < 2:
        return None
    c = Counter(votes).most_common()
    if c[0][1] >= 2 and (len(c) == 1 or c[0][1] > c[1][1]):
        return c[0][0]
    return SPLIT


def kappa(x: Mapping[Any, str], y: Mapping[Any, str], collapse: Mapping[str, str] | None = None,
          labels: Sequence[str] = LABELS) -> dict[str, Any] | None:
    """Cohen's κ between two raters on the keys both labelled with a known label.

    ``collapse`` maps labels before comparing (e.g. :data:`MERGE_PARTIAL`). Returns
    ``{kappa, agree, n}``; ``kappa`` is ``None`` when chance agreement is 1."""
    ks = [k for k in x if k in y and x[k] in labels and y[k] in labels]
    if not ks:
        return None

    def f(v: str) -> str:
        return collapse.get(v, v) if collapse else v

    po = sum(f(x[k]) == f(y[k]) for k in ks) / len(ks)
    px = Counter(f(x[k]) for k in ks)
    py = Counter(f(y[k]) for k in ks)
    pe = sum(px[lab] * py[lab] for lab in set(px) | set(py)) / len(ks) ** 2
    return {"kappa": round((po - pe) / (1 - pe), 3) if pe < 1 else None, "agree": round(po, 3), "n": len(ks)}


def counts(labels: Mapping[ItemKey, str | None], item_arms: Mapping[str, Mapping[str, Any]],
           docs: Iterable[str], arms: Sequence[str]) -> dict[str, Counter[str]]:
    """Per arm, the label counts of the items that arm proposed, over ``docs`` (a
    document may repeat: bootstrap resamples). Unlabelled and split items are left out."""
    out: dict[str, Counter[str]] = {a: Counter() for a in arms}
    docs = list(docs)
    for a in arms:
        c = out[a]
        for d in docs:
            for i, m in item_arms[d]["items"].items():
                lab = labels.get((d, i))
                if a in m["arms"] and lab in LABELS:
                    c[lab] += 1
    return out


def rates(c: Mapping[str, int]) -> dict[str, Any] | None:
    """``{n, strict, lenient, harm, not_a_symbol}`` shares of one arm's labelled items."""
    n = sum(c.values())
    if not n:
        return None
    return {"n": n, "strict": c.get("correct", 0) / n,
            "lenient": (c.get("correct", 0) + c.get("partial", 0)) / n,
            "harm": c.get("wrong", 0) / n, "not_a_symbol": c.get("not-a-symbol", 0) / n}


def _q(xs: list[float]) -> list[float] | None:
    if not xs:
        return None
    s = sorted(xs)
    return [round(s[int(0.025 * len(s))], 3), round(s[int(0.975 * len(s)) - 1], 3)]


def doc_bootstrap(labels: Mapping[ItemKey, str | None], item_arms: Mapping[str, Mapping[str, Any]],
                  docs: Sequence[str], arms: Sequence[str], seed: int, n_boot: int = 2000
                  ) -> dict[str, dict[str, list[float] | None]]:
    """95% document-clustered percentile CIs of strict / lenient / harm per arm.

    Each resample draws ``len(docs)`` documents with replacement from
    ``random.Random(seed)``; percentiles at indices ``int(.025 B)`` and
    ``int(.975 B) − 1`` of the sorted values (round 1's convention)."""
    rng = random.Random(seed)
    acc: dict[str, dict[str, list[float]]] = {a: {"strict": [], "lenient": [], "harm": []} for a in arms}
    for _ in range(n_boot):
        s = [rng.choice(docs) for _ in docs]
        for a, c in counts(labels, item_arms, s, arms).items():
            r = rates(c)
            if r:
                for k in acc[a]:
                    acc[a][k].append(r[k])
    return {a: {k: _q(v) for k, v in acc[a].items()} for a in arms}


def diff_bootstrap(labels: Mapping[ItemKey, str | None], item_arms: Mapping[str, Mapping[str, Any]],
                   docs: Sequence[str], arms: Sequence[str], a: str, b: str, key: str, seed: int,
                   n_boot: int = 2000) -> list[float] | None:
    """95% document-clustered CI of ``rate_a[key] − rate_b[key]`` (resamples where either
    arm has no labelled item are skipped)."""
    rng = random.Random(seed)
    ds: list[float] = []
    for _ in range(n_boot):
        s = [rng.choice(docs) for _ in docs]
        c = counts(labels, item_arms, s, arms)
        ra, rb = rates(c[a]), rates(c[b])
        if ra and rb:
            ds.append(ra[key] - rb[key])
    return _q(ds)
