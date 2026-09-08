"""Stage 0b' -- the plumbing reproduction and the SS7.6 manipulation checks.

**The plumbing check the brief sets** is that the dense-mode 4k reach reproduces Stage 0
SS5's ``P(doc packed)`` (``floor_diagnostic.json``). Stage 0 counted that budget in the
**generator's** tokenizer and Stage 0b' counts in the **embedder's** (r3 SS3.3), and the
generator's tokenizer is not available to this run, so the check is decomposed into three
parts rather than fudged into one:

* **A -- retrieval identity.** The ``vector`` pool this harness builds is compared chunk by
  chunk against Stage 0's frozen ``pool_<arm>.json`` on the same twenty CDS queries. Same
  embeddings, same depth, same reranker: this should be an identity, and any drift is a
  harness bug, not a tokenizer effect.
* **B -- scoring identity.** ``P(doc packed)`` is recomputed **from Stage 0's own frozen
  packed unions** with this harness's code. This must reproduce ``floor_diagnostic.json``
  exactly (to 1e-4); it isolates the scoring path from the packing path.
* **C -- end-to-end reach.** This harness packs its own dense pools at the SFR-token budget
  that corresponds to Stage 0's 4,096 generator tokens under r3 SS3.3's measured ratio
  (4,096 x 2048/1630 = **5,145 SFR tokens**) and compares ``P(doc packed)`` to Stage 0's.
  This is the ``+- 0.01`` target; the residual is a tokenizer-conversion residual and is
  reported as such.

**Manipulation checks (r3 SS3.1, re-read on the split endpoints).** GOLD packing control,
NEGATIVE control (0 by construction, kept as plumbing), discrimination, and the
budget-bind check at the new primary budget.
"""
from __future__ import annotations

import gzip
import json
import statistics as st
import sys
import time

import s0b_common as K
import s0_common as C  # noqa: F401
from s0b_pack import pack_groups
from s0b_score import contained, doc_epack

GEN4096_IN_SFR = round(4096 * K.SFR_PER_GEN)      # 5145


def check_a() -> dict:
    """The vector pool against Stage 0's frozen pool, on the twenty CDS queries."""
    out = {}
    for arm in K.INDEX_KEYS:
        old_p = K.WORK / f"pool_{arm}.json"
        new_p = K.OUT / f"pool_{arm}.jsonl"
        if not (old_p.exists() and new_p.exists()):
            out[arm] = "ABSENT"
            continue
        old = json.loads(old_p.read_text())
        new = {}
        for line in open(new_p):
            r = json.loads(line)
            if r["population"] == "cds":
                new[r["qid"]] = r
        ov, rk, ov1 = [], [], []
        for t in K.DEV_TOPICS:
            for v in K.CDS_VARIANTS:
                o = old[v][t]["reranked"]
                oidx = [int(x[4]) for x in o]
                r = new[f"{t}::{v}"]
                nidx = sorted((int(j) for j in r["pools"]["vector"]),
                              key=lambda j: -r["chunks"][str(j)]["ce"])
                ov.append(len(set(oidx) & set(nidx)) / max(len(oidx), 1))
                rk.append(1.0 if oidx == nidx else 0.0)
                ov1.append(1.0 if oidx[0] == nidx[0] else 0.0)
        out[arm] = {"mean_pool_overlap_at_50": round(st.mean(ov), 6),
                    "identical_order_rate": round(st.mean(rk), 4),
                    "same_rank1_rate": round(st.mean(ov1), 4),
                    "queries": len(ov)}
    return out


def _stage0_units():
    U = json.loads((K.WORK / "units.json").read_text())["units"]
    return U


def check_b() -> dict:
    """P(doc packed) recomputed from Stage 0's own frozen unions."""
    U = _stage0_units()
    packed = json.loads((K.WORK / "packed.json").read_text())
    floor = json.loads((K.WORK / "floor_diagnostic.json").read_text())
    ref = floor["decomposition_at_B4096"]
    out = {}
    for arm in sorted(packed):
        dp = []
        for t, us in U.items():
            if not us:
                continue
            rec = packed[arm]["summary"][t][str(4096)]
            un = set(rec["union"])
            for u in us:
                dp.append(int(u["docno"] in un))
        got = round(st.mean(dp), 4)
        want = ref[arm]["P_doc_packed"]
        out[arm] = {"recomputed": got, "stage0": want,
                    "abs_diff": round(abs(got - want), 6),
                    "units": len(dp)}
    out["_max_abs_diff"] = max(v["abs_diff"] for k, v in out.items()
                               if not k.startswith("_"))
    return out


def check_c(docs, units, headers) -> dict:
    """This harness's own dense pools, packed at the SFR budget equivalent to 4,096 gen."""
    from s0b_pack import Packer
    tok = K.SfrCount()
    pk = Packer(docs, units, headers, tok)
    U = _stage0_units()
    floor = json.loads((K.WORK / "floor_diagnostic.json").read_text())
    ref = floor["decomposition_at_B4096"]
    out = {}
    for arm in K.SCORING_ARMS:
        if arm not in ref:
            continue
        src = arm if arm in K.INDEX_KEYS else K.DELIVERY_ARMS.get(arm, (arm,))[0]
        p = K.OUT / f"pool_{src}.jsonl"
        if not p.exists():
            continue
        recs = {}
        for line in open(p):
            r = json.loads(line)
            if r["population"] == "cds" and r["variant"] == "summary":
                recs[r["topic"]] = r
        dp = []
        for t, us in U.items():
            if not us or t not in recs:
                continue
            rec = recs[t]
            order = sorted((int(j) for j in rec["pools"]["vector"]),
                           key=lambda j: -rec["chunks"][str(j)]["ce"])
            g = pk.groups_for(arm, rec, order)
            got = pack_groups(g, (GEN4096_IN_SFR, 4096))
            un = set(got[str(GEN4096_IN_SFR)]["union"])
            for u in us:
                dp.append(int(u["docno"] in un))
        got = round(st.mean(dp), 4)
        want = ref[arm]["P_doc_packed"]
        out[arm] = {"reach_at_5145_sfr": got, "stage0_at_4096_gen": want,
                    "abs_diff": round(abs(got - want), 6), "units": len(dp)}
    out["_max_abs_diff"] = max(v["abs_diff"] for k, v in out.items()
                               if not k.startswith("_"))
    out["_budget"] = {"sfr": GEN4096_IN_SFR, "equivalent_generator_tokens": 4096,
                      "ratio": round(K.SFR_PER_GEN, 4)}
    return out


# ------------------------------------------------------------------ SS7.6 checks
def manipulation(cds, pointed) -> dict:
    """GOLD, NEGATIVE, discrimination and budget-bind, on the split endpoints."""
    out: dict = {}

    # ---- GOLD packing control: pack exactly the gold text of every evidence document ---
    eret, ea, eb, ec = [], [], [], []
    for t, docs in cds["topics"].items():
        for d, g in docs.items():
            if g["kind"] != "pooled" or not g["evidence_bearing"]:
                continue
            iv = sorted([tuple(v) for v in g["sentence_spans"].values()] +
                        [(sp["start"], sp["end"]) for u in g["units"] for sp in u["spans"]])
            merged = []
            for a, b in iv:
                if merged and a <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            merged = [tuple(x) for x in merged]
            eret.append(1)
            v = doc_epack(g, merged)
            ea.append(v["a"])
            if v["b"] is not None:
                eb.append(v["b"])
            if v["c"] is not None:
                ec.append(v["c"])
    out["GOLD_packing_control"] = {
        "ERET": round(st.mean(eret), 4) if eret else None,
        "EPACK_a_support_weighted": round(st.mean(ea), 4) if ea else None,
        "EPACK_b_core": round(st.mean(eb), 4) if eb else None,
        "EPACK_c_unit": round(st.mean(ec), 4) if ec else None,
        "documents": len(eret),
        "bar": "EPACK >= 0.95 and ERET = 1.0 (r3 SS3.1)"}

    # ---- NEGATIVE control: 0 by construction; verified, not assumed ------------------
    grade0 = {(t, d) for t, docs in cds["topics"].items() for d, g in docs.items()
              if g["grade"] == 0}
    ev = {(t, d) for t, docs in cds["topics"].items() for d, g in docs.items()
          if g["evidence_bearing"] and g["kind"] == "pooled"}
    out["NEGATIVE_control"] = {
        "grade0_pairs_in_labeling_set": len(grade0),
        "grade0_pairs_that_are_evidence_bearing": len(grade0 & ev),
        "ERET_from_grade0_only": 0.0,
        "note": "0 by construction (evidence-bearing documents are grade >= 1); kept as a "
                "plumbing check, not a finding (r3 SS3.1)"}

    # ---- gold-span sanity for the pointed population --------------------------------
    docs_txt = None
    bad = [q for q, g in pointed.items()
           if any(s[0] >= s[1] for s in g["spans"])]
    out["pointed_gold_spans"] = {"queries": len(pointed), "degenerate_spans": len(bad),
                                 "one_query_per_document":
                                     len({g["docno"] for g in pointed.values()}) == len(pointed)}
    del docs_txt
    return out


def discrimination_and_bind() -> dict:
    """Top-10 document sets between the size extremes, and whether the budget binds."""
    top10: dict = {}
    bind: dict = {}
    for pop in ("cds", "pointed"):
        src = K.CTX / f"packed-{pop}.jsonl.gz"
        with gzip.open(src, "rt") as f:
            for line in f:
                r = json.loads(line)
                if r["rerank"] != "on":
                    continue
                pk = r["budgets"][str(K.PRIMARY_BUDGET)]
                if r["arm"] in ("fixed_tok256_ov0pct", "fixed_tok2048_ov0pct"):
                    seen, docs10 = set(), []
                    for d, _s, _e, _n in pk["items"]:
                        if d not in seen:
                            seen.add(d)
                            docs10.append(d)
                        if len(docs10) == 10:
                            break
                    top10.setdefault((pop, r["mode"], r["qid"]), {})[r["arm"]] = docs10
                bind.setdefault((pop, r["arm"], r["mode"]), []).append(
                    (pk["n_sources"], pk["sfr_tokens"]))
    disc: dict = {}
    for (pop, mode, _q), v in top10.items():
        if len(v) < 2:
            continue
        a = set(v["fixed_tok256_ov0pct"])
        b = set(v["fixed_tok2048_ov0pct"])
        disc.setdefault((pop, mode), []).append(0.0 if a == b else 1.0)
    out = {"discrimination_top10_doc_sets_differ": {
        f"{p}/{m}": {"rate": round(st.mean(v), 4), "n_queries": len(v),
                     "bar": ">= 0.25 (r3 SS11 guard 1)"}
        for (p, m), v in sorted(disc.items())}}
    out["budget_bind_at_primary"] = {
        f"{p}/{a}/{m}": {"mean_sources_admitted": round(st.mean(x[0] for x in v), 2),
                         "mean_sfr_realised": round(st.mean(x[1] for x in v)),
                         "binds_rate": round(st.mean(1.0 if x[0] < K.DEPTH else 0.0
                                                     for x in v), 4)}
        for (p, a, m), v in sorted(bind.items())}
    return out


def main() -> None:
    t0 = time.time()
    docs = K.load_docs()
    units = K.load_units()
    headers = K.load_headers()
    cds = json.loads((K.OUT / "gold_cds.json").read_text())
    pointed = json.loads((K.OUT / "gold_pointed.json").read_text())
    out = {
        "plumbing_A_retrieval_identity_vs_stage0": check_a(),
        "plumbing_B_scoring_identity_on_stage0_unions": check_b(),
        "plumbing_C_end_to_end_reach": check_c(docs, units, headers),
        "manipulation": manipulation(cds, pointed),
        "discrimination_and_budget_bind": discrimination_and_bind(),
        "seconds": round(time.time() - t0, 1),
        "provenance": K.provenance(),
    }
    a = out["plumbing_C_end_to_end_reach"]["_max_abs_diff"]
    b = out["plumbing_B_scoring_identity_on_stage0_unions"]["_max_abs_diff"]
    out["VERDICT"] = {
        "scoring_identity_max_abs_diff": b,
        "end_to_end_max_abs_diff": a,
        "gate": "target +- 0.01, STOP above 0.02 (brief)",
        "status": "PASS" if (b <= 1e-4 and a <= 0.01)
                  else ("MARGINAL" if a <= 0.02 else "FAIL")}
    K.atomic_json(K.OUT / "checks.json", out)
    K.atomic_json(K.ART / "checks.json", out)
    print(json.dumps({k: out[k] for k in ("plumbing_B_scoring_identity_on_stage0_unions",
                                          "plumbing_C_end_to_end_reach", "VERDICT")},
                     indent=1), flush=True)


if __name__ == "__main__":
    sys.exit(main())
