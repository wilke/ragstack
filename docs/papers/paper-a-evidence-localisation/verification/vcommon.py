"""Shared helpers for the Paper A independent recomputation.

Written from the definitions in DRAFT.md and the RESULTS-*.md files, NOT imported from the
Stage 0 harness (`docs/plans/results/stage0/s0_*.py`). Nothing here contacts an endpoint,
a store or a model; everything reads committed label files.

Every recompute script writes one JSON file whose `provenance` key is
`ragstack.provenance.experiment_provenance()` taken at the start of the run (before any
output exists, so a clean tree records `citable: true`).
"""
from __future__ import annotations

import json
import math
import os
import pathlib
import statistics
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[3]
STAGE0 = REPO / "docs" / "plans" / "results" / "stage0"
ART = STAGE0 / "artifacts"
CLAIMS = HERE.parent / "claims.json"

# Label files the recomputation reads. All are committed, except the post-filtered DEV labels
# (outside any work/conf path) used only for the span-filter-dev-verification check.
F = {
    "s0": ART / "labels-dev.jsonl",
    "r3": {j: ART / "r3" / f"labels-r3-{j}.jsonl" for j in ("scout", "qwen")},
    "r31": {j: ART / "r31" / f"labels-r31-{j}.jsonl" for j in ("scout", "qwen")},
    "r31ext": {j: ART / "r31ext" / f"labels-r31-{j}.jsonl" for j in ("scout", "qwen")},
    "claude": {j: ART / "claude" / f"labels-claude-{j}.jsonl"
               for j in ("sonnet5", "opus5", "fable51")},
}
FILTERED_DEV = pathlib.Path(os.environ.get(
    "PAPER_A_FILTERED_DEV", "/rag/tmp/stage0-conf/work/r31ext-filtered"))
assert "/conf/" not in str(FILTERED_DEV) + "/", "quarantine: never read under work/conf"

JACCARD_BAR = 0.5


def provenance() -> dict:
    sys.path.insert(0, str(REPO / "python"))
    try:
        from ragstack.provenance import experiment_provenance
        return experiment_provenance(repo=REPO)
    except Exception as e:  # noqa: BLE001
        import subprocess
        head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        return {"commit": head, "note": f"experiment_provenance unavailable: {e!r}"}


def read_jsonl(p) -> list[dict]:
    with open(p) as f:
        return [json.loads(x) for x in f if x.strip()]


def by_pair_presentation(recs) -> dict:
    """{(topic, docno): {presentation: record}}; records without `presentation` are k = 0."""
    out: dict = {}
    for r in recs:
        out.setdefault((r["topic"], r["docno"]), {})[r.get("presentation", 0)] = r
    return out


def load(run: str, judge: str) -> dict:
    return by_pair_presentation(read_jsonl(F[run][judge]))


# ------------------------------------------------------------------ evidence objects
def positive(rec) -> bool:
    """The 'whether' verdict as the record carries it: at least one verified evidence set."""
    return len(rec["sets"]) > 0


def char_spans(sets) -> list[tuple[int, int]]:
    return [(sp["start"], sp["end"]) for s in sets for sp in s["spans"]]


def sentence_set(rec) -> frozenset:
    """Evidence sentences of one reading as (unit, sentence index), every set pooled."""
    out = set()
    for s in rec["sets"]:
        for sp in s["spans"]:
            for i in range(sp["first_sentence"], sp["last_sentence"] + 1):
                out.add((sp["unit"], i))
    return frozenset(out)


def _merge(iv):
    iv = sorted(iv)
    out: list[list[int]] = []
    for a, b in iv:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def covered(iv) -> int:
    return sum(b - a for a, b in _merge(iv))


def char_jaccard(x, y) -> float:
    """|X n Y| / |X u Y| over character positions; 0.0 when both are empty."""
    X, Y = _merge(x), _merge(y)
    inter = 0
    i = j = 0
    while i < len(X) and j < len(Y):
        lo, hi = max(X[i][0], Y[j][0]), min(X[i][1], Y[j][1])
        if hi > lo:
            inter += hi - lo
        if X[i][1] < Y[j][1]:
            i += 1
        else:
            j += 1
    union = covered(X) + covered(Y) - inter
    return inter / union if union else 0.0


def consistent_union(a_sets, b_sets) -> bool:
    """Span self-consistency: union Jaccard >= 0.5; two empty verdicts agree."""
    if not a_sets and not b_sets:
        return True
    if not a_sets or not b_sets:
        return False
    return char_jaccard(char_spans(a_sets), char_spans(b_sets)) >= JACCARD_BAR


def distinct_sets(seq_of_setlists) -> list[list[tuple[int, int]]]:
    """Accumulate evidence sets in order; a new set is distinct unless its span-union Jaccard
    with an already accepted set reaches 0.5 (the D3 merge rule as DRAFT §5.3 states it)."""
    acc: list[list[tuple[int, int]]] = []
    for sets in seq_of_setlists:
        for s in sets:
            iv = [(sp["start"], sp["end"]) for sp in s["spans"]]
            if not any(char_jaccard(iv, a) >= JACCARD_BAR for a in acc):
                acc.append(iv)
    return acc


# ------------------------------------------------------------------ statistics
Z95 = 1.959963984540054


def wilson_upper(k: int, n: int, z: float = Z95) -> float:
    p = k / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return min(1.0, (centre + half) / (1 + z * z / n))


def pearson(x, y):
    n = len(x)
    if n < 2:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def pearson_float(x, y):
    """Pearson r with a plain float no-variance test (sqrt of the sum of squares == 0.0).
    Used only to show how the committed values arose; `pearson` is the definition."""
    n = len(x)
    if n < 2:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


def avg_ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for t in range(i, j + 1):
            r[order[t]] = (i + j) / 2 + 1
        i = j + 1
    return r


def cohen_kappa(a, b) -> dict:
    n = len(a)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return {"n": n, "observed_agreement": po, "kappa": (po - pe) / (1 - pe),
            "a_positive_rate": pa, "b_positive_rate": pb}


def spearman_brown(r, m=2.0):
    return m * r / (1 + (m - 1) * r)


def sb_invert(R, k):
    """Implied single-reading reliability from a k-reading reliability R."""
    return R / (k - (k - 1) * R)


def mm_fit(ks, us, km_hi=1e6) -> dict:
    """Least-squares U(k) = Vmax k / (Km + k). Vmax is closed-form given Km; Km is found by
    a log-spaced scan followed by golden-section refinement (independent of the harness's
    linear coarse-to-fine grid). `km_hi` is far above the harness's 500 ceiling, so a
    linear curve shows up as Km running away rather than parking at a grid edge."""
    def sse(km):
        x = [k / (km + k) for k in ks]
        v = sum(u * xi for u, xi in zip(us, x)) / sum(xi * xi for xi in x)
        return sum((u - v * xi) ** 2 for u, xi in zip(us, x)), v

    grid = [10 ** (e / 200) for e in range(-800, int(200 * math.log10(km_hi)) + 1)]
    vals = [sse(g)[0] for g in grid]
    i = min(range(len(grid)), key=vals.__getitem__)
    lo, hi = grid[max(i - 1, 0)], grid[min(i + 1, len(grid) - 1)]
    g = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    c, d = b - g * (b - a), a + g * (b - a)
    for _ in range(200):
        if sse(c)[0] < sse(d)[0]:
            b = d
        else:
            a = c
        c, d = b - g * (b - a), a + g * (b - a)
    km = (a + b) / 2
    s, vmax = sse(km)
    mu = sum(us) / len(us)
    sst = sum((u - mu) ** 2 for u in us)
    return {"Vmax": vmax, "Km": km, "r2": 1 - s / sst, "sse": s,
            "km_at_scan_edge": i >= len(grid) - 2}


def linear_r2(ks, us) -> float:
    n = len(ks)
    mk, mu = sum(ks) / n, sum(us) / n
    a = sum((k - mk) * (u - mu) for k, u in zip(ks, us)) / sum((k - mk) ** 2 for k in ks)
    b = mu - a * mk
    sse = sum((u - a * k - b) ** 2 for k, u in zip(ks, us))
    return 1 - sse / sum((u - mu) ** 2 for u in us)


def fmean(x):
    return statistics.fmean(x) if x else None


def median(x):
    return statistics.median(x) if x else None


def write(name: str, prov: dict, body: dict, outdir=None) -> pathlib.Path:
    od = pathlib.Path(outdir or os.environ.get("PAPER_A_VERIFY_OUT", HERE / "out"))
    od.mkdir(parents=True, exist_ok=True)
    p = od / f"{name}.json"
    p.write_text(json.dumps({"script": name, "provenance": prov, **body}, indent=1,
                            default=str) + "\n")
    print(f"wrote {p}")
    return p
