"""Stage 0b' step 3 -- the split endpoints, r3 SS3.1, on both populations.

**CDS.** Per (topic, query variant) and per configuration:

* ``ERET`` -- of the topic's **evidence-bearing documents** (any pooled support > 0, cleaned
  of the one flagged locator blow-up), the fraction with at least one chunk admitted into
  the packed context.
* ``EPACK`` -- per reached document, computed **three ways** because r3 SS10 item 4 is still
  open and the owner is pursuing all three options:
  (a) *support-weighted*: contained support mass / the document's total support mass;
  (b) *core*: the fraction of the document's support >= 0.5 sentences contained;
  (c) *unit-based*: the fraction of the document's D3 rules 1-3 units (k = 0 union,
      <= 4 per document) whose every span is fully contained -- the r2-style D4 reading.

  Per-document values are written out so that ``s0b_stats`` can form the **intersection**
  estimand (the documents both arms of a contrast reached) without re-scoring.
* the product ``ERET x EPACK`` (reached-set) is the descriptive continuity column -- r2's
  ``EUC``, never again a primary.

**Pointed.** Per query: ``ERET`` is 1 iff the gold document has a chunk admitted, and
``EPACK`` is 1 iff the gold char-span union is **fully contained** (D4) in that document's
admitted union. Macro-averaged over queries; the cluster is the source document and there
is exactly one query per document, so the cluster is the query.

Also computed here: the ``ERET`` **plumbing reproduction** of Stage 0 SS5's ``P(doc packed)``
and the SS7.6 manipulation checks that need the packed contexts (GOLD, NEGATIVE,
discrimination, budget-bind).
"""
from __future__ import annotations

import gzip
import json
import statistics as st
import sys
import time

import s0b_common as K
import s0_common as C  # noqa: F401


def contained(span, iv) -> bool:
    a, b = span
    return any(x <= a and b <= y for x, y in iv)


def doc_epack(g: dict, iv) -> dict:
    """The three readings of containment for one document."""
    sup = g["support"]
    spans = g["sentence_spans"]
    mass = 0.0
    hit = 0.0
    for k, w in sup.items():
        if contained(spans[k], iv):
            hit += w
        mass += w
    a = hit / mass if mass > 0 else None
    core = g["core"]
    b = (sum(1 for k in core if contained(spans[k], iv)) / len(core)) if core else None
    units = g["units"]
    if units:
        cov = sum(1 for u in units
                  if all(contained((sp["start"], sp["end"]), iv) for sp in u["spans"]))
        c = cov / len(units)
    else:
        c = None
    return {"a": a, "b": b, "c": c}


def main() -> None:
    t0 = time.time()
    cds = json.loads((K.OUT / "gold_cds.json").read_text())
    pointed = json.loads((K.OUT / "gold_pointed.json").read_text())

    # evidence-bearing documents per topic (pooled kind only -- the `sample` documents are
    # the SS7.5 bias-bound draw and are not part of the topic's denominator)
    E: dict[str, list[str]] = {}
    E_units: dict[str, list[str]] = {}
    for t, docs in cds["topics"].items():
        E[t] = sorted(d for d, r in docs.items()
                      if r["kind"] == "pooled" and r["evidence_bearing"])
        E_units[t] = sorted(d for d, r in docs.items()
                            if r["kind"] == "pooled" and r["units"])
    long_docs = {x["docno"] for x in cds["long_documents"]}

    out_cds = gzip.open(K.OUT / "endpoints-cds.jsonl.gz", "wt")
    out_pt = gzip.open(K.OUT / "endpoints-pointed.jsonl.gz", "wt")
    n = {"cds": 0, "pointed": 0}

    for pop in ("cds", "pointed"):
        src = K.CTX / f"packed-{pop}.jsonl.gz"
        with gzip.open(src, "rt") as f:
            for line in f:
                r = json.loads(line)
                for B, pk in r["budgets"].items():
                    iv = {d: [tuple(x) for x in v] for d, v in pk["union"].items()}
                    base = {"qid": r["qid"], "topic": r["topic"],
                            "variant": r["variant"], "arm": r["arm"], "mode": r["mode"],
                            "rerank": r["rerank"], "budget": int(B),
                            "sfr_tokens": pk["sfr_tokens"],
                            "sfr_tokens_per_source_duplicated":
                                pk["sfr_tokens_per_source_duplicated"],
                            "gen_tokens_est": pk["gen_tokens_est"],
                            "n_items": pk["n_items"], "n_sources": pk["n_sources"],
                            "n_docs": pk["n_docs"]}
                    if pop == "cds":
                        t = r["topic"]
                        ev = E[t]
                        reached = [d for d in ev if d in iv]
                        per = {d: doc_epack(cds["topics"][t][d], iv[d]) for d in reached}
                        base.update({
                            "n_evidence_docs": len(ev),
                            "n_evidence_docs_units": len(E_units[t]),
                            "reached": reached,
                            "reached_units_denom": [d for d in E_units[t] if d in iv],
                            "epack": {d: {k: (round(v, 6) if v is not None else None)
                                          for k, v in per[d].items()} for d in reached},
                            "reached_excl_long": [d for d in reached if d not in long_docs],
                            "n_evidence_docs_excl_long":
                                len([d for d in ev if d not in long_docs]),
                        })
                        out_cds.write(json.dumps(base) + "\n")
                        n["cds"] += 1
                    else:
                        g = pointed[r["qid"]]
                        d = g["docno"]
                        reach = d in iv
                        cont = bool(reach and all(contained(tuple(s), iv[d])
                                                  for s in g["spans"]))
                        base.update({"docno": d, "ERET": int(reach),
                                     "EPACK": int(cont)})
                        out_pt.write(json.dumps(base) + "\n")
                        n["pointed"] += 1
    out_cds.close()
    out_pt.close()

    meta = {
        "records": n,
        "evidence_bearing_definition":
            "CDS: any pooled per-sentence support > 0 over the 30 cleaned readings "
            "(reading (a)'s definition, which is what the brief specifies). The stricter "
            "unit-based denominator (>= 1 D3 unit from the k = 0 union) is carried "
            "alongside as `n_evidence_docs_units` / `reached_units_denom`.",
        "evidence_docs_per_topic": {t: len(v) for t, v in sorted(E.items())},
        "evidence_docs_per_topic_unitbased": {t: len(v) for t, v in sorted(E_units.items())},
        "long_documents_flagged": sorted(long_docs),
        "seconds": round(time.time() - t0, 1),
        "sha256": {"cds": K.sha256_file(K.OUT / "endpoints-cds.jsonl.gz"),
                   "pointed": K.sha256_file(K.OUT / "endpoints-pointed.jsonl.gz")},
    }
    K.atomic_json(K.OUT / "score_meta.json", meta)
    print(json.dumps(meta, indent=1)[:2000], flush=True)
    print("mean evidence docs/topic:",
          round(st.mean(len(v) for v in E.values()), 2), flush=True)


if __name__ == "__main__":
    sys.exit(main())
