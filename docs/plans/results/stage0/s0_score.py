"""Stage 0b step 4 -- evidence units (D3), coverage (D4), EUC, and the SS7.6 checks.

D3 is applied here MECHANICALLY, in the SPEC's order, with counts recorded at each step:
within-document merge at character-span-union Jaccard >= 0.5 (canonical list = the SMALLER
set, so merging can never make a unit easier to cover), within-document containment pruning
(keep the subset, drop the superset), NO cross-document merging, then the seeded cap at 12
units per topic **stratified by source document**.

D4: a unit is covered iff EVERY span of its canonical list is FULLY contained in the packed
context's per-document character-span union. The >= 0.9-character-overlap variant is
computed as a descriptive column and is never primary.
"""
from __future__ import annotations

import json
import random

import s0_common as C


# ------------------------------------------------------------------ interval algebra
def merge_iv(iv):
    if not iv:
        return []
    s = sorted(iv)
    out = [list(s[0])]
    for a, b in s[1:]:
        if a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def total(iv):
    return sum(b - a for a, b in iv)


def inter(x, y):
    out, i, j = [], 0, 0
    while i < len(x) and j < len(y):
        a = max(x[i][0], y[j][0])
        b = min(x[i][1], y[j][1])
        if a < b:
            out.append([a, b])
        if x[i][1] < y[j][1]:
            i += 1
        else:
            j += 1
    return out


def jaccard(a, b):
    A, B = merge_iv(a), merge_iv(b)
    it = total(inter(A, B))
    un = total(A) + total(B) - it
    return it / un if un else 0.0


# ------------------------------------------------------------------------- D3
def build_units(labels: list[dict]) -> dict:
    by_topic: dict[str, list[dict]] = {}
    steps = {"raw_sets": 0, "after_merge": 0, "after_containment": 0, "after_cap": 0,
             "no_localizable_evidence_pairs": 0, "dropped_pairs": 0,
             "pairs": 0, "windowed_pairs": 0}
    per_grade_none = {}
    for rec in labels:
        steps["pairs"] += 1
        if rec["windowed"]:
            steps["windowed_pairs"] += 1
        if rec["dropped"]:
            steps["dropped_pairs"] += 1
            continue
        sets = rec["sets"]
        steps["raw_sets"] += len(sets)
        if not sets:
            steps["no_localizable_evidence_pairs"] += 1
            g = str(rec["grade"])
            per_grade_none[g] = per_grade_none.get(g, 0) + 1
            continue
        # -- 1. within-document merge at span-union Jaccard >= 0.5 -----------------
        cur = [{"spans": s["spans"],
                "iv": merge_iv([[sp["start"], sp["end"]] for sp in s["spans"]])}
               for s in sets]
        changed = True
        while changed:
            changed = False
            for i in range(len(cur)):
                for j in range(i + 1, len(cur)):
                    if jaccard(cur[i]["iv"], cur[j]["iv"]) >= C.JACCARD_MERGE:
                        keep = cur[i] if total(cur[i]["iv"]) <= total(cur[j]["iv"]) \
                            else cur[j]
                        cur = [c for k, c in enumerate(cur) if k not in (i, j)] + [keep]
                        changed = True
                        break
                if changed:
                    break
        steps["after_merge"] += len(cur)
        # -- 2. within-document containment: keep the subset, drop the superset ----
        keys = [frozenset((sp["unit"], sp["first_sentence"], sp["last_sentence"])
                          for sp in c["spans"]) for c in cur]
        drop = set()
        for i in range(len(cur)):
            for j in range(len(cur)):
                if i != j and keys[i] < keys[j]:
                    drop.add(j)
        cur = [c for k, c in enumerate(cur) if k not in drop]
        steps["after_containment"] += len(cur)
        for c in cur:
            by_topic.setdefault(rec["topic"], []).append(
                {"docno": rec["docno"], "grade": rec["grade"], "kind": rec["kind"],
                 "spans": c["spans"], "iv": c["iv"]})
    # -- 4. cap 12 per topic, seeded, STRATIFIED BY SOURCE DOCUMENT ---------------
    capped = {}
    cap_hits = 0
    for t, us in by_topic.items():
        pooled = [u for u in us if u["kind"] == "pooled"]
        if len(pooled) <= C.UNIT_CAP:
            capped[t] = pooled
        else:
            cap_hits += 1
            rng = random.Random(f"{C.SEED_UNITCAP}:{t}")   # seeded per topic; string seed so a non-numeric id cannot crash it
            bydoc: dict[str, list] = {}
            for u in pooled:
                bydoc.setdefault(u["docno"], []).append(u)
            for d in bydoc:
                rng.shuffle(bydoc[d])
            docs = sorted(bydoc)
            rng.shuffle(docs)
            take, k = [], 0
            while len(take) < C.UNIT_CAP:
                progressed = False
                for d in docs:                      # round-robin over source documents
                    if k < len(bydoc[d]):
                        take.append(bydoc[d][k])
                        progressed = True
                        if len(take) == C.UNIT_CAP:
                            break
                if not progressed:
                    break
                k += 1
            capped[t] = take
        steps["after_cap"] += len(capped[t])
    steps["cap_hit_topics"] = cap_hits
    steps["none_by_grade"] = per_grade_none
    # the bias-bound sample's units are kept separately (SS7.5 evidence-recall secondary)
    sample_units = {t: [u for u in us if u["kind"] == "sample"]
                    for t, us in by_topic.items()}
    return {"units": capped, "sample_units": sample_units, "steps": steps}


# ------------------------------------------------------------------------- D4
def covered(unit, union: dict, tol: float | None = None) -> bool:
    iv = union.get(unit["docno"])
    if not iv:
        return False
    for sp in unit["spans"]:
        s, e = sp["start"], sp["end"]
        if tol is None:
            if not any(a <= s and e <= b for a, b in iv):
                return False
        else:
            ov = total(inter([[s, e]], iv))
            if e > s and ov / (e - s) < tol:
                return False
    return True


def main() -> None:
    labels = [json.loads(x) for x in (C.WORK / "labels.jsonl").read_text().splitlines() if x]
    U = build_units(labels)
    units, steps = U["units"], U["steps"]
    # A topic every one of whose pairs returned "no localizable evidence" produces no
    # units and would otherwise VANISH from the record. Make it visible with m = 0 so the
    # SS8.5.6 "< 3 evidence units" exclusion can be read off the table.
    for t in C.DEV_TOPICS:
        units.setdefault(t, [])
    packed = json.loads((C.WORK / "packed.json").read_text())
    arms = sorted(packed)

    m = {t: len(v) for t, v in units.items()}
    print("units per topic:", json.dumps(m), flush=True)

    euc: dict = {}
    matrix: dict = {}
    for arm in arms:
        for v in ("summary", "description"):
            for B in C.BUDGETS:
                for t, us in units.items():
                    rec = packed[arm][v].get(t)
                    if rec is None or not us:
                        continue
                    un = {d: [tuple(x) for x in iv]
                          for d, iv in rec[str(B)]["union"].items()}
                    cov = [covered(u, un) for u in us]
                    cov9 = [covered(u, un, tol=0.9) for u in us]
                    euc.setdefault(arm, {}).setdefault(v, {}).setdefault(str(B), {})[t] = {
                        "EUC": sum(cov) / len(us),
                        "EUC_tol09": sum(cov9) / len(us),
                        "ES_Hit": int(any(cov)), "m": len(us),
                        "raw_tokens": rec[str(B)]["raw_tokens"],
                        "n_chunks": rec[str(B)]["n_chunks"]}
                    matrix.setdefault(arm, {}).setdefault(v, {}).setdefault(
                        str(B), {})[t] = [int(x) for x in cov]

    C.atomic_json(C.WORK / "euc.json", euc)
    C.atomic_json(C.WORK / "unit_matrix.json", matrix)
    C.atomic_json(C.WORK / "units.json",
                  {"units": {t: [{k: u[k] for k in ("docno", "grade", "spans")}
                                 for u in us] for t, us in units.items()},
                   "sample_units_n": {t: len(v) for t, v in U["sample_units"].items()},
                   "steps": steps, "m_per_topic": m})
    print(json.dumps(steps, indent=1), flush=True)


if __name__ == "__main__":
    main()
