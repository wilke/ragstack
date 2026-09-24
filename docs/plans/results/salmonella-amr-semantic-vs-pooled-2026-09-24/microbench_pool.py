"""Per-window microbenchmark of the two pure-Python vector ops in SemanticChunker.

Runs inside ragstack-worker-v1.6.4.sif against no store and no endpoint. Times
``_mean_pool`` over a 7-vector window (buffer 3 → 2*3+1) of 4096-d Python lists and
``_cosine_distance`` over one pair, then projects onto this corpus's 13,294 windows
/ 13,274 pairs (offline/summary-run1.json). The projection is what the record's
Surprise 1 quotes; the corroboration is the non-embed wall gap between the arms.
"""
import json
import random
import sys
import time

from ragstack.ingestion.chunkers import _cosine_distance, _mean_pool

DIM, WINDOW, N = 4096, 7, 2000
WINDOWS, PAIRS = 13294, 13274  # summary-run1.json: texts_embedded, n_distance_pairs
random.seed(0)
vecs = [[random.random() for _ in range(DIM)] for _ in range(N + WINDOW)]

t0 = time.perf_counter()
for i in range(N):
    _mean_pool(vecs[i:i + WINDOW])
pool_ms = (time.perf_counter() - t0) / N * 1e3

t0 = time.perf_counter()
for i in range(N):
    _cosine_distance(vecs[i], vecs[i + 1])
cos_ms = (time.perf_counter() - t0) / N * 1e3

out = {"dim": DIM, "window": WINDOW, "n_iter": N, "python": sys.version.split()[0],
       "mean_pool_ms_per_window": round(pool_ms, 3), "cosine_distance_ms_per_pair": round(cos_ms, 3),
       "projected_mean_pool_s": round(pool_ms * WINDOWS / 1e3, 1),
       "projected_cosine_s": round(cos_ms * PAIRS / 1e3, 1)}
print(json.dumps(out, indent=1))
if len(sys.argv) > 1:
    json.dump(out, open(sys.argv[1], "w"), indent=1)
