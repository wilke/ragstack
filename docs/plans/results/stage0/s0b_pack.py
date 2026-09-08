"""Stage 0b' step 2 -- packing at 4,096 / 16,384 / 32,768 **SFR** tokens.

The rule is revision 2 SS7.3's A1, unchanged and restated: *walk the ordered list from rank
1; admit each item whole if it fits in the remaining budget; **stop at the first item that
does not fit**; rank 1 is admitted even if it alone exceeds B; an item whose text is
already admitted is skipped at zero cost and does not end the walk.* What revision 3
changes is only the tokenizer the budget is counted in (r3 SS3.3: SFR on both sides).

Eleven scoring arms are packed from six retrieval pools:

* the **six index arms** -- their own chunks;
* ``parent256`` -- each admitted chunk of ``fixed_tok256_ov0pct`` replaced by its enclosing
  top-level JATS ``<sec>`` unit, truncated to <= 1,024 SFR tokens centred on the child; a
  repeated parent slice is packed once (Stage 0b's pin, carried);
* ``nbr1_512`` / ``nbr1_256`` / ``nbr2_512`` -- each admitted chunk brings its +-w
  neighbours. The **group** (source plus its not-yet-admitted neighbours) is the packing
  unit, which is what makes r3 SS3.5's "+-1 at 512 costs 3x per source" true; supplied text
  is **de-duplicated by chunk id** (a neighbour already admitted, as a source or as another
  source's neighbour, is free). The per-source-duplicated total is reported beside it as
  the descriptive column SS3.5 asks for. Neighbours are the adjacent rows of the same
  document in the arm's own chunk list -- the chunk records carry no ``prev_chunk_id`` /
  ``next_chunk_id`` field, so adjacency is derived and asserted, not assumed.
* ``multi256+1024`` -- RRF (k = 60) over the ``vector`` rankings of ``fixed_tok256_ov0pct``
  and ``fixed_tok1024_ov0pct``, as r3 SS3.4 specifies (``vector`` only).

``header512``'s header is **charged to the budget** (it is supplied text) and contributes
**no character span**: the containment union is over indexed content only.

Output: ``work/stage0b-prime/contexts/packed-<population>.jsonl.gz`` -- one record per
(arm, mode, rerank, budget, query) carrying the admitted chunk list, the per-document
character-span union and the realised token totals. That file **is** the packed context in
reference form; ``contexts/text/`` materialises the primary configuration as literal text.
"""
from __future__ import annotations

import gzip
import json
import sys
import time

import s0b_common as K
import s0_common as C  # noqa: F401,E402  (imported for path side effects)


# ------------------------------------------------------------------ parent spans
def parent_span(units, text, cs, ce, tok) -> tuple[int, int]:
    ps = pe = None
    for u in units:
        if u["start_char"] <= cs < u["end_char"]:
            ps, pe = u["start_char"], u["end_char"]
            break
    if ps is None:
        return cs, ce
    if tok.count(text[ps:pe]) <= K.PARENT_MAX_SFR:
        return ps, pe
    mid = (cs + ce) // 2
    lo, hi = 0, max(pe - ps, ce - cs)
    for _ in range(24):
        h = (lo + hi) // 2
        a, b = max(ps, mid - h), min(pe, mid + h)
        if tok.count(text[a:b]) <= K.PARENT_MAX_SFR:
            lo = h
        else:
            hi = h
        if hi - lo <= 1:
            break
    a, b = max(ps, mid - lo), min(pe, mid + lo)
    return (a, b) if b > a else (cs, ce)


# ------------------------------------------------------------------ the walk
def pack_groups(groups, budgets):
    """``groups`` = ordered list of [(key, docno, start, end, sfr, has_span), ...].

    A group is admitted whole or not at all; its cost is the sum of the tokens of its
    members whose ``key`` has not already been supplied. Returns per budget.
    """
    out = {}
    for B in budgets:
        cum = 0
        raw = 0
        admitted = []
        seen: set = set()
        n_groups = 0
        for gi, g in enumerate(groups):
            new = [m for m in g if m[0] not in seen]
            cost = sum(m[4] for m in new)
            gross = sum(m[4] for m in g)
            if gi == 0 or cum + cost <= B:
                for m in new:
                    seen.add(m[0])
                    admitted.append(m)
                cum += cost
                raw += gross
                n_groups += 1
                if not new:
                    continue
            else:
                break
        union: dict[str, list] = {}
        for _k, d, s, e, _n, has_span in admitted:
            if has_span:
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
        out[str(B)] = {
            "sfr_tokens": cum,
            "sfr_tokens_per_source_duplicated": raw,
            "gen_tokens_est": round(K.gen_tokens(cum), 1),
            "n_items": len(admitted), "n_sources": n_groups,
            "n_docs": len(union),
            "items": [[d, s, e, n] for _k, d, s, e, n, _h in admitted],
            "union": union}
    return out


# ------------------------------------------------------------------ item builders
def ordered_pool(rec, mode: str, rerank: str) -> list[int]:
    pool = [int(x) for x in rec["pools"][mode]]
    if rerank == "on":
        return sorted(pool, key=lambda j: -rec["chunks"][str(j)]["ce"])
    return pool


class Packer:
    def __init__(self, docs, units, headers, tok):
        self.docs, self.units, self.headers, self.tok = docs, units, headers, tok
        self.parent_cache: dict = {}
        self.hdr_tokens: dict = {}
        self.nbr: dict[str, tuple[dict, dict]] = {}
        self.rows: dict[str, list] = {}

    def arm_rows(self, arm: str):
        if arm not in self.rows:
            self.rows[arm] = K.load_rows(arm)
        return self.rows[arm]

    def neighbours(self, arm: str):
        if arm not in self.nbr:
            self.nbr[arm] = K.neighbour_table(self.arm_rows(arm))
        return self.nbr[arm]

    def header_tokens(self, docno: str, start: int) -> int:
        h = self.headers.get((docno, start), "")
        if not h:
            return 0
        key = (docno, start)
        if key not in self.hdr_tokens:
            self.hdr_tokens[key] = self.tok.count(h)
        return self.hdr_tokens[key]

    def parent(self, docno: str, s: int, e: int):
        key = (docno, s)
        if key not in self.parent_cache:
            self.parent_cache[key] = parent_span(
                self.units.get(docno, []), self.docs[docno], s, e, self.tok)
        return self.parent_cache[key]

    # -- one group list per (scoring arm, ordered source pool) ------------------
    def groups_for(self, arm: str, rec, order: list[int]):
        ch = rec["chunks"]
        if arm in K.INDEX_KEYS:
            g = []
            for j in order:
                c = ch[str(j)]
                extra = self.header_tokens(c["docno"], c["start"]) if arm == "header512" \
                    else 0
                g.append([(f"c{j}", c["docno"], c["start"], c["end"],
                           c["sfr"] + extra, True)])
            return g
        if arm == "parent256":
            g = []
            for j in order:
                c = ch[str(j)]
                a, b = self.parent(c["docno"], c["start"], c["end"])
                g.append([(f"p{c['docno']}:{a}:{b}", c["docno"], a, b,
                           self.tok.count(self.docs[c["docno"]][a:b]), True)])
            return g
        if arm in K.DELIVERY_ARMS and K.DELIVERY_ARMS[arm][1] == "neighbour":
            src, _kind, w = K.DELIVERY_ARMS[arm]
            prev, nxt = self.neighbours(src)
            rows = self.arm_rows(src)
            g = []
            for j in order:
                members = [j]
                k = j
                for _ in range(w):
                    k = prev.get(k)
                    if k is None:
                        break
                    members.insert(0, k)
                k = j
                for _ in range(w):
                    k = nxt.get(k)
                    if k is None:
                        break
                    members.append(k)
                g.append([(f"c{m}", rows[m][0], rows[m][1], rows[m][2], rows[m][3], True)
                          for m in members])
            return g
        raise KeyError(arm)


def multi_order(recs: dict, rerank: str) -> list[tuple[str, int]]:
    """RRF over the two source arms' ``vector`` rankings (r3 SS3.4: vector only)."""
    lists = []
    for a in K.MULTI_SOURCES:
        rec = recs[a]
        lists.append([(a, j) for j in ordered_pool(rec, "vector", rerank)])
    score: dict = {}
    for lst in lists:
        for r, x in enumerate(lst, start=1):
            score[x] = score.get(x, 0.0) + 1.0 / (K.RRF_K + r)
    return [x for x, _s in sorted(score.items(),
                                  key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))][:K.DEPTH]


def main() -> None:
    t0 = time.time()
    docs = K.load_docs()
    units = K.load_units()
    headers = K.load_headers()
    tok = K.SfrCount()
    pk = Packer(docs, units, headers, tok)

    pools = {}
    for arm in K.INDEX_KEYS:
        p = K.OUT / f"pool_{arm}.jsonl"
        assert p.exists(), f"missing pool for {arm}; run s0b_retrieve.py first"
        by = {}
        for line in open(p):
            r = json.loads(line)
            by[r["qid"]] = r
        pools[arm] = by
    qids = list(pools[K.INDEX_KEYS[0]])
    for a in K.INDEX_KEYS:
        assert list(pools[a]) == qids, a
    print(f"pools loaded: {len(K.INDEX_KEYS)} arms x {len(qids)} queries "
          f"{time.time()-t0:.0f}s", flush=True)

    handles = {p: gzip.open(K.CTX / f"packed-{p}.jsonl.gz", "wt")
               for p in ("cds", "pointed")}
    counts = {"cds": 0, "pointed": 0}
    for qi, qid in enumerate(qids):
        pop = pools[K.INDEX_KEYS[0]][qid]["population"]
        recs = {a: pools[a][qid] for a in K.INDEX_KEYS}
        base = {"qid": qid, "population": pop, "topic": recs[K.INDEX_KEYS[0]]["topic"],
                "variant": recs[K.INDEX_KEYS[0]]["variant"]}
        for rerank in K.RERANK_STATES:
            for mode in K.MODES:
                for arm in K.SCORING_ARMS:
                    if arm == K.MULTI_ARM:
                        if mode != "vector":
                            continue
                        order = multi_order(recs, rerank)
                        g = []
                        for a, j in order:
                            c = recs[a]["chunks"][str(j)]
                            g.append([(f"{a}:{j}", c["docno"], c["start"], c["end"],
                                       c["sfr"], True)])
                    else:
                        src = arm if arm in K.INDEX_KEYS else K.DELIVERY_ARMS[arm][0]
                        rec = recs[src]
                        g = pk.groups_for(arm, rec, ordered_pool(rec, mode, rerank))
                    packed = pack_groups(g, K.BUDGETS)
                    handles[pop].write(json.dumps(
                        {**base, "arm": arm, "mode": mode, "rerank": rerank,
                         "budgets": packed}) + "\n")
                    counts[pop] += 1
        if (qi + 1) % 20 == 0:
            print(f"  packed {qi+1}/{len(qids)} {time.time()-t0:.0f}s", flush=True)
    for h in handles.values():
        h.close()

    meta = {
        "rule": "A1: stop-at-first-non-fit, rank-1 always admitted, never truncated, "
                "already-supplied text free and does not end the walk",
        "budget_tokenizer": K.SFR_TOKENIZER_ID,
        "budget_units": "SFR tokens (r3 SS3.3, Stage 0b' path)",
        "generator_tokens": K.GEN_TOKENIZER_STATUS,
        "budgets": list(K.BUDGETS), "primary": K.PRIMARY_BUDGET,
        "scoring_arms": K.SCORING_ARMS, "modes": list(K.MODES),
        "rerank_states": list(K.RERANK_STATES),
        "records": counts,
        "PINS": {
            "neighbour_group_is_the_packing_unit":
                "a source and its not-yet-admitted neighbours are admitted together or "
                "not at all; this is what makes r3 SS3.5's '3x per source' cost true, and "
                "it is the budget model of production's expand_context under a packing "
                "walk",
            "neighbour_dedup": "by chunk id, across sources (r3 SS3.5); the "
                               "per-source-duplicated total is reported as "
                               "sfr_tokens_per_source_duplicated",
            "neighbour_adjacency": "adjacent rows of the same document in the arm's own "
                                   "chunk list; the chunk records carry no prev/next id "
                                   "field, so adjacency is derived and asserted",
            "header512_span": "header CHARGED to the budget, contributes NO character span",
            "parent256_cap": f"{K.PARENT_MAX_SFR} SFR tokens -- SS5.2's 1,024 read in the "
                             "one tokenizer r3 SS3.3 mandates, not converted",
            "multi256+1024": "vector rankings only (r3 SS3.4); rerank-on fuses each arm's "
                             "reranked vector order, rerank-off the dense order",
        },
        "tokenizer_calls": tok.calls,
        "seconds": round(time.time() - t0, 1),
        "sha256": {p: K.sha256_file(K.CTX / f"packed-{p}.jsonl.gz")
                   for p in ("cds", "pointed")},
        "provenance": K.provenance(),
    }
    K.atomic_json(K.OUT / "pack_meta.json", meta)
    print(json.dumps({k: meta[k] for k in ("records", "tokenizer_calls", "seconds",
                                           "sha256")}, indent=1), flush=True)


if __name__ == "__main__":
    sys.exit(main())
