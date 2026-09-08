"""Pointed-at-scale -- shared plumbing for the reach-vs-corpus-size study.

r3 SS10 item 5, option (b). Stage 0b' (#520) found the **pointed population at its
ceiling**: on the 32,663-document corpus the gold document is reached by 90-96 % of the
177 queries for every arm, so the population cannot separate arms. The owner's target
corpus is ~500k PMC OA articles. The question this module's harness answers is **how fast
reach falls as the corpus grows**.

Two steps, and this module serves both:

* **step 1 (free)** -- subsample the *existing* corpus DOWN to {4k, 8k, 16k, 32,663}
  documents, three seeded draws per size, gold documents always retained, and measure
  pointed ``ERET`` / ``EPACK | reach`` at each size. Fit reach against ``log10(N)`` and
  **extrapolate** to 150k and 500k. No embedding of corpus chunks: a subsample is a
  **row mask** over the frozen matrices under ``emb/``, which are opened read-only.
* **step 2 (paid, gated)** -- grow the corpus 5x and measure the 150k point directly.
  Only if step 1's extrapolation puts >= 2 arms inside [0.15, 0.90] at 150k.

**Everything Stage 0b' pinned is reused, not re-declared**: ``s0b_common`` (paths, arms,
modes, budgets, the BM25 pin, the SFR tokenizer), ``s0b_bm25`` (the lexical leg),
``s0b_pack.pack_groups`` (the A1 walk), ``s0b_retrieve.rrf`` (the fusion), and the pointed
gold shape of ``s0b_gold.build_pointed``. The only *new* degree of freedom here is which
documents are in the corpus.

**Endpoints.** ``:50052`` (crossencoder, <= 4 in flight) and -- for step 2 only --
``:9001-:9006`` (<= 2 in flight each). No store of any kind is contacted: no Qdrant, no
Elasticsearch, no tenant API, never ``mango``. GPUs 6 and 7 are untouched.

**Writes.** Step 1 writes only under ``work/pointed-scale/``; step 2 writes only under
``scale/``. ``emb/``, ``chunks/`` and ``xml/`` are read-only to this study.
"""
from __future__ import annotations

import json
import os
import pathlib
import random
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import s0b_common as K          # noqa: E402  (must precede s0_common: sys.path shim)
import s0_common as C           # noqa: E402

BIG = K.BIG
EMB = K.EMB                     # READ-ONLY to this study
CHUNKS = K.CHUNKS               # READ-ONLY to this study
WORK = K.WORK
OUT = WORK / "pointed-scale"    # the ONLY directory step 1 writes under BIG
SCALE = BIG / "scale"           # the ONLY directory step 2 writes under BIG
for _d in (OUT,):
    _d.mkdir(parents=True, exist_ok=True)

ART = HERE / "artifacts" / "pointed-scale"
ART.mkdir(parents=True, exist_ok=True)

RUN = HERE / "run"
RUN.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------ the design
SEED_SUBSAMPLE = C.SEED_BIASBOUND + 1      # 20260917 -- the brief's seed
SIZES = (4000, 8000, 16000, 32663)         # 32,663 = the whole existing corpus
DRAWS = 3
FULL_N = 32663
SMOKE_SIZE = 500

# The one budget and the one shape the projection is read at: Stage 0b's primary.
BUDGET = K.PRIMARY_BUDGET                  # 16,384 SFR tokens
PRIMARY_MODE = "hybrid"
PRIMARY_RERANK = "on"
MODES = K.MODES
INDEX_KEYS = K.INDEX_KEYS
WINDOW = K.WINDOW                          # (0.15, 0.90) -- r3 SS11 guard 1

# Extrapolation targets (r3 SS1 / PLAN-C: the owner's corpus is ~500k PMC OA articles).
TARGETS = (150_000, 500_000)
SEED_BOOT = K.SEED_BOOT                    # 20260913, Stage 0b's bootstrap seed
N_BOOT = 2000


def subsets(all_docnos: list[str], gold: set[str], sizes=SIZES, draws=DRAWS,
            seed: int = SEED_SUBSAMPLE) -> list[dict]:
    """Nested random subsets, gold documents always retained.

    One RNG per draw over ``sorted(non-gold)``; the subset of size ``N`` is
    ``gold | shuffled[:N - |gold|]``, so the sizes are **exact** and the family is
    **nested** by construction (a prefix of one permutation). The full corpus is one
    draw, not three: three draws of "everything" are the same set, and saying so is
    cheaper than pretending to a replicate that does not exist.
    """
    pool0 = sorted(gold)
    rest0 = sorted(set(all_docnos) - gold)
    assert len(pool0) + len(rest0) == len(all_docnos)
    out = []
    for d in range(draws):
        rng = random.Random(f"{seed}:{d}")
        rest = list(rest0)
        rng.shuffle(rest)
        for n in sizes:
            if n >= len(all_docnos):
                if d > 0:
                    continue            # the full corpus has exactly one realisation
                docs = sorted(all_docnos)
                key = f"n{len(docs)}_full"
            else:
                assert n >= len(pool0), (n, len(pool0))
                docs = sorted(pool0 + rest[: n - len(pool0)])
                key = f"n{n}_d{d}"
            out.append({"key": key, "size": len(docs), "draw": d, "docnos": docs})
    return out


def load_pointed() -> list[dict]:
    """The 177 pointed development queries, with their gold spans merged (Stage 0b')."""
    from s0_score import merge_iv
    out = []
    for line in K.POINTED.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        out.append({
            "qid": r["qid"], "text": r["query"], "docno": r["docno"],
            "topic": r["draw_topic"],
            "spans": merge_iv([[g["start"], g["end"]] for g in r["gold_spans"]]),
        })
    K.assert_dev_only([q["topic"] for q in out])
    assert len({q["docno"] for q in out}) == len(out), "one query per source document"
    return out


def git_head(repo: str = C.REPO) -> str:
    return K.git_head(repo)


def provenance(extra: dict | None = None) -> dict:
    base = K.provenance()
    base.update({
        "study": "pointed-at-scale (r3 SS10 item 5, option b)",
        "subsample_seed": SEED_SUBSAMPLE,
        "sizes": list(SIZES), "draws": DRAWS,
        "budget_sfr": BUDGET, "primary_mode": PRIMARY_MODE,
        "primary_rerank": PRIMARY_RERANK,
        "extrapolation_targets": list(TARGETS),
        "emb_dir_is_read_only": str(EMB),
        "writes_under": [str(OUT), str(SCALE)],
    })
    base.update(extra or {})
    return base


def atomic_json(path, obj):
    return K.atomic_json(path, obj)


def gpu_snapshot():
    q = subprocess.run(
        ["nvidia-smi", "--query-gpu=index,memory.used,utilization.gpu",
         "--format=csv,noheader,nounits"], capture_output=True, text=True, check=True)
    return [{"gpu": int(a), "mem_used_mib": int(b), "util_pct": int(c)}
            for a, b, c in (l.split(", ") for l in q.stdout.strip().splitlines())]


def df_rag_gb() -> float:
    st = os.statvfs("/rag")
    return round(st.f_bavail * st.f_frsize / 1e9, 1)
