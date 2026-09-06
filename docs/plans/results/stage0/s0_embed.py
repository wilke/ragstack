"""Stage 0a step 5 -- embed the six index arms on the SFR fleet (SS5.1, SS11, P.1).

``stage1_common.Fleet``: ``:9001-:9006`` only, <=2 in flight per endpoint (the house rule
in its per-endpoint form, SS12.5). **GPUs 6 and 7 are never touched** -- no endpoint above
:9006 is contacted and nothing here selects a device.

The corpus manifest hash is RE-VERIFIED here against the value recorded before chunking,
so an arm can never be embedded against a corpus that moved under it.

Per arm: ``emb_<arm>.npy`` (float32, N x 4096, memmapped and filled in blocks so a killed
run resumes) + ``rows_<arm>.json`` (docno / span / ntok per row) + ``estats_<arm>.json``.

Arm order is deliberate: the NI-family arms first, so a tripwire stop still leaves N1/N2
calibrated (SS11 stop rule -- per-arm artifacts are the checkpoints).
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np

import s0_common as C

ORDER = ["fixed_tok512", "fixed_tok1024_ov0pct", "fixed_tok512_ov0pct",
         "fixed_tok256_ov0pct", "fixed_tok2048_ov0pct", "header512"]
BLOCK = 20000                      # chunks per checkpointed block
TRIPWIRE_FLEET_HOURS = 8.0         # brief's cap, in the SS11 fleet-wall-clock accounting


def load_docs() -> dict[str, str]:
    d = {}
    for line in open(C.WORK / "docs.jsonl"):
        r = json.loads(line)
        d[r["docno"]] = r["text"]
    return d


def rows_for(arm: str) -> list[tuple[str, int, int, int, str]]:
    out = []
    for line in open(C.CHUNKS / f"spans_{arm}.jsonl"):
        r = json.loads(line)
        hdr = r.get("hdr")
        for i, (s, e, n) in enumerate(r["spans"]):
            out.append((r["docno"], s, e, n, hdr[i] if hdr else ""))
    return out


def embed_arm(arm: str, docs: dict[str, str], fleet) -> dict:
    rows = rows_for(arm)
    n = len(rows)
    npy = C.EMB / f"emb_{arm}.npy"
    prog = C.EMB / f"emb_{arm}.progress.json"
    C.atomic_json(C.EMB / f"rows_{arm}.json",
                  [[d, s, e, t] for d, s, e, t, _h in rows])
    if npy.exists() and prog.exists():
        done = json.loads(prog.read_text())["done"]
        arr = np.lib.format.open_memmap(npy, mode="r+")
        assert arr.shape == (n, 4096), (arr.shape, n)
    else:
        done = 0
        arr = np.lib.format.open_memmap(npy, mode="w+", dtype=np.float32,
                                        shape=(n, 4096))
    t0 = time.time()
    tok0 = fleet.actual_tokens
    print(f"[{arm}] {n} chunks, resuming at {done}", flush=True)
    while done < n:
        blk = rows[done:done + BLOCK]
        texts = [h + docs[d][s:e] for d, s, e, _t, h in blk]
        ntoks = [t for _d, _s, _e, t, _h in blk]
        vecs = fleet.embed(texts, ntoks, label=f"{arm}:{done}", every=400)
        arr[done:done + len(blk)] = vecs
        arr.flush()
        done += len(blk)
        C.atomic_json(prog, {"done": done, "n": n})
        el = time.time() - t0
        rate = (fleet.actual_tokens - tok0) / max(el, 1e-9)
        print(f"[{arm}] {done}/{n}  {el:.0f}s  {rate/1000:.0f}k tok/s", flush=True)
    el = time.time() - t0
    st = {"arm": arm, "chunks": n, "seconds": round(el, 1),
          "sfr_tokens": sum(r[3] for r in rows),
          "tok_per_s": round(sum(r[3] for r in rows) / max(el, 1e-9), 1)}
    C.atomic_json(C.EMB / f"estats_{arm}.json", st)
    print(json.dumps(st), flush=True)
    return st


def main() -> None:
    man = json.loads((C.WORK / "manifest.json").read_text())
    expect = man["manifest_sha256"]
    import hashlib
    blob = "\n".join(f"{a} {b}" for a, b in man["pairs"])
    assert hashlib.sha256(blob.encode()).hexdigest() == expect, "manifest hash mismatch"
    print("corpus manifest sha256 re-verified:", expect, flush=True)

    ids = C.served_ids()
    print(json.dumps({k: v for k, v in ids.items() if k.startswith("sfr")}), flush=True)
    gpu0 = C.gpu_snapshot()
    assert all(g["mem_used_mib"] == 0 for g in gpu0 if g["gpu"] in (6, 7)), \
        f"GPUs 6/7 are RESERVED and are not idle: {gpu0}"

    cstats = json.loads((C.WORK / "chunk_stats.json").read_text())
    total_tok = cstats["_total_sfr_tokens"]
    proj_h = total_tok / 161_000 / 3600
    print(f"PROJECTION: {total_tok/1e9:.3f}B SFR tokens; at the measured 161k tok/s "
          f"= {proj_h:.2f} fleet-wall-clock hours (band {proj_h/2:.2f}-{proj_h*2:.2f}); "
          f"per-device that is {proj_h*6:.1f} GPU-hours across the 6 endpoints.", flush=True)
    if proj_h > TRIPWIRE_FLEET_HOURS:
        raise SystemExit(f"projected {proj_h:.2f} fleet-h exceeds the {TRIPWIRE_FLEET_HOURS} h cap")

    docs = load_docs()
    print("docs loaded:", len(docs), flush=True)
    fleet = __import__("stage1_common").Fleet()
    stats = {}
    wall0 = time.time()
    for arm in ORDER:
        stats[arm] = embed_arm(arm, docs, fleet)
        spent = (time.time() - wall0) / 3600
        done_tok = sum(s["sfr_tokens"] for s in stats.values())
        proj_total = spent * total_tok / max(done_tok, 1)
        print(f"== after {arm}: {spent:.2f} h spent, projected total {proj_total:.2f} h",
              flush=True)
        C.atomic_json(C.EMB / "estats_all.json",
                      {"arms": stats, "fleet": fleet.stats(),
                       "hours_spent": round(spent, 3),
                       "projected_total_hours": round(proj_total, 3)})
        if proj_total > TRIPWIRE_FLEET_HOURS or proj_total > 2 * proj_h:
            print(f"!! TRIPWIRE: projected {proj_total:.2f} h "
                  f"(cap {TRIPWIRE_FLEET_HOURS}, 2x central {2*proj_h:.2f}). Stopping with "
                  f"{len(stats)}/{len(ORDER)} arms built.", flush=True)
            sys.exit(3)
    gpu1 = C.gpu_snapshot()
    C.atomic_json(C.EMB / "estats_all.json",
                  {"arms": stats, "fleet": fleet.stats(),
                   "hours_spent": round((time.time() - wall0) / 3600, 3),
                   "gpu_before": gpu0, "gpu_after": gpu1, "served": ids})
    print("ALL ARMS DONE", flush=True)


if __name__ == "__main__":
    sys.path.insert(0, str(C.STAGE1))
    main()
