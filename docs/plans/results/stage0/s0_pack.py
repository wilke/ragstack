"""Stage 0b step 2 -- the packing rule A1, and the per-document char-span union (SS7.3, P.6).

The rule, in the SPEC's own one sentence: *walk the reranked list by rank from 1; admit
each chunk whole if it fits in the remaining budget; **stop at the first chunk that does
not fit** (no skip-ahead, no partial final chunk, never a truncated chunk); rank 1 is
admitted even if it alone exceeds B; an already-admitted parent is skipped at zero cost
and does not end the walk.*

Budget tokens are the **served generator's** tokenizer, probed live on ``mango:8003``
(``/tokenize``, ``add_special_tokens=false`` -- pinned: budgets count each chunk's own
supplied text, so no BOS is charged per chunk). SFR and reranker token counts are
descriptive columns and never enter a budget decision (SS7.2).

``parent256`` (SS5.2) is built here, not by a new index: each admitted chunk of
``fixed_tok256_ov0pct`` is replaced by its enclosing **top-level** unit -- for a chunk that
straddles a boundary, the unit containing the chunk's ``start_char`` -- truncated to
<= 1024 generator tokens centred on the child chunk; a repeated parent is packed once.

``header512``'s header tokens are charged to the budget (they are supplied text) but
contribute NO character span: the coverage union is over indexed content only.
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import s0_common as C

PARENT_MAX = 1024          # SS5.2 generator tokens


class Tok:
    """Thread-safe cached wrapper over the served-generator ``/tokenize`` probe."""

    def __init__(self, conc: int = 4):
        import threading
        self.t = C.GenTokenizer()
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(conc)      # mango politeness: <= 4

    def warm(self, texts):
        todo = []
        seen = set()
        for x in texts:
            import hashlib
            h = hashlib.blake2b(x.encode(), digest_size=16).hexdigest()
            if h not in self.t.cache and h not in seen:
                seen.add(h)
                todo.append(x)
        if todo:
            list(self.pool.map(self.t.count, todo))

    def count(self, text: str) -> int:
        return self.t.count(text)


def parent_span(units, text, cs, ce_, tok) -> tuple[int, int]:
    """SS5.2: the enclosing top-level unit of ``cs``, shrunk to <= 1024 gen tokens."""
    ps, pe = None, None
    for u in units:
        if u["start_char"] <= cs < u["end_char"]:
            ps, pe = u["start_char"], u["end_char"]
            break
    if ps is None:                                    # no unit covers it: keep the chunk
        return cs, ce_
    if tok.count(text[ps:pe]) <= PARENT_MAX:
        return ps, pe
    mid = (cs + ce_) // 2
    lo, hi = 0, max(pe - ps, ce_ - cs)
    for _ in range(24):                               # binary search the half-width
        h = (lo + hi) // 2
        a, b = max(ps, mid - h), min(pe, mid + h)
        if tok.count(text[a:b]) <= PARENT_MAX:
            lo = h
        else:
            hi = h
        if hi - lo <= 1:
            break
    a, b = max(ps, mid - lo), min(pe, mid + lo)
    if b <= a:
        a, b = cs, ce_
    return a, b


def pack_one(items, budgets, docs=None, tok=None):
    """``items`` = [(docno, start, end, gen_tokens)] in RANK order. Returns per budget.

    Both totals SS7.3 requires are reported: ``raw_tokens`` (overlapping text charged every
    time it is supplied -- the overlap tax under measurement) and ``dedup_tokens`` (the
    per-document character-span union re-tokenized once).
    """
    out = {}
    for B in budgets:
        cum = 0
        admitted = []
        seen_span = set()
        for i, (d, s, e, nt) in enumerate(items):
            key = (d, s, e)
            if key in seen_span:                     # duplicate parent: free, walk continues
                continue
            if i == 0 or cum + nt <= B:              # rank 1 admitted even if alone over B
                admitted.append((d, s, e, nt))
                seen_span.add(key)
                cum += nt
            else:
                break                                # stop at the first non-fit
        union = {}
        for d, s, e, _nt in admitted:
            union.setdefault(d, []).append([s, e])
        for d in union:
            iv = sorted(union[d])
            m = [iv[0]]
            for a, b in iv[1:]:
                if a <= m[-1][1]:
                    m[-1][1] = max(m[-1][1], b)
                else:
                    m.append([a, b])
            union[d] = m
        ded = None
        if docs is not None and tok is not None:
            slices = [docs[d][a:b] for d, iv in union.items() for a, b in iv]
            tok.warm(slices)
            ded = sum(tok.count(x) for x in slices)
        out[str(B)] = {"raw_tokens": cum, "n_chunks": len(admitted), "union": union,
                       "dedup_tokens": ded}
    return out


def main() -> None:
    docs, units = {}, {}
    for line in open(C.WORK / "docs.jsonl"):
        r = json.loads(line)
        docs[r["docno"]] = r["text"]
    for line in open(C.WORK / "units.jsonl"):
        r = json.loads(line)
        units[r["docno"]] = r["units"]
    hdr = {}
    for line in open(C.CHUNKS / "spans_header512.jsonl"):
        r = json.loads(line)
        for i, (s, _e, _n) in enumerate(r["spans"]):
            hdr[(r["docno"], s)] = r["hdr"][i]

    tok = Tok()
    arms = [a for a in C.INDEX_KEYS if (C.WORK / f"pool_{a}.json").exists()]
    pools = {a: json.loads((C.WORK / f"pool_{a}.json").read_text()) for a in arms}
    out = {}
    t0 = time.time()

    for arm in arms:
        for v in ("summary", "description"):
            for t, rec in pools[arm][v].items():
                cand = rec["reranked"]
                texts = [(hdr.get((d, s), "") if arm == "header512" else "")
                         + docs[d][s:e] for d, s, e, _sc, _ri in cand]
                tok.warm(texts)
                items = [(d, s, e, tok.count(x))
                         for (d, s, e, _sc, _ri), x in zip(cand, texts)]
                out.setdefault(arm, {}).setdefault(v, {})[t] = pack_one(
                    items, C.BUDGETS, docs, tok)
        print(f"[{arm}] packed {time.time()-t0:.0f}s", flush=True)

    # ---- parent256 (SS5.2): same retrieval and rerank as tok256/0, different packing --
    if "fixed_tok256_ov0pct" in pools:
        for v in ("summary", "description"):
            for t, rec in pools["fixed_tok256_ov0pct"][v].items():
                items = []
                for d, s, e, _sc, _ri in rec["reranked"]:
                    a, b = parent_span(units.get(d, []), docs[d], s, e, tok)
                    items.append((d, a, b, tok.count(docs[d][a:b])))
                out.setdefault("parent256", {}).setdefault(v, {})[t] = \
                    pack_one(items, C.BUDGETS, docs, tok)
        print(f"[parent256] packed {time.time()-t0:.0f}s", flush=True)

    C.atomic_json(C.WORK / "packed.json", out)
    C.atomic_json(C.WORK / "pack_meta.json", {
        "generator_tokenizer": {"endpoint": C.MANGO + "/tokenize",
                                "served_model": C.SCOUT_EXPECT,
                                "add_special_tokens": C.GenTokenizer.ADD_SPECIAL},
        "budgets": list(C.BUDGETS), "depth": C.DEPTH, "parent_max_tokens": PARENT_MAX,
        "rule": "A1 disambiguated: stop-at-first-non-fit, rank-1 always admitted, "
                "never truncated, duplicate parent free and does not end the walk",
        "PINS": {
            "header512_section_for_straddling_chunk":
                "the unit whose span contains the chunk's start_char (SS5.2's rule for "
                "parent256, applied to header512 where SS5.1 is silent)",
            "header512_span": "the header is CHARGED to the budget but contributes NO "
                              "character span; the coverage union is indexed content only",
            "parent256_duplicate_identity":
                "a parent is 'already admitted' iff the (docno, start, end) of the packed "
                "slice matches. A section larger than 1024 generator tokens yields "
                "DIFFERENT truncation windows for different children, so those are "
                "charged separately -- an interpretation of SS5.2's 'repeated parents are "
                "packed once', pinned here rather than left implicit",
            "generator_tokenizer_add_special_tokens": False,
            "budget_counts": "each chunk's own supplied text; inter-chunk join overhead "
                             "is not charged",
        },
        "seconds": round(time.time() - t0, 1)})
    print("packing done", flush=True)


if __name__ == "__main__":
    sys.path.insert(0, str(C.STAGE1))
    main()
