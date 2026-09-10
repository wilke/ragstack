"""Confirmation run (a), step 5 — labeling the pooled pairs. **QUARANTINED.**

The calibrated recipe of `s0_label_r31.py` (#507) at the depth `s0_labelgates_r31ext.py`
(#512) measured usable — **Scout × 20 presentations + Qwen × 10 = 30 pooled readings**,
graded per-sentence support reliable at 0.92 — applied to the confirmation labeling set.

**Nothing about the instrument changes.** The prompt is `s0_label_r31.PROMPT`, imported and
its sha256 **asserted byte-for-byte** against the digest the committed r3.1 manifest
records; the system message, the re-prompt, the locator, the D1 snap, the §6.5 windowing,
the `<think>` stripper, the retry ladder and the presentation seeding
(``SEED_LABELDUP + 100*k + pair_index``) are all imported from `s0_label_r31` /
`s0_label_r3`. Temperature 0, ≤ 4 in flight per endpoint. The mixture whose reliability was
measured is the mixture that runs.

**Two mechanical departures, both provably outcome-neutral, both recorded:**

1. **The §6.5 window's token counts are precomputed** (``--prepare-tokens``) into
   ``work/conf/gentok/unit_tokens.json`` instead of being fetched inside the labeling loop.
   `s0_label_r31` calls ``mango:8003/tokenize`` from the same process that has four chat
   requests in flight, which is five in flight to one endpoint; this task's endpoint budget
   is four. Precomputing also means the Qwen process never contacts ``:8003`` at all, and
   that both judges window identically. A document whose **whole text** is ≤
   ``WINDOW_TOKENS`` characters cannot exceed ``WINDOW_TOKENS`` tokens (a token is at least
   one character), so it is recorded as a single window without being tokenized; every
   longer document is tokenized unit by unit with the same tokenizer, the same
   ``add_special_tokens=False``, and the counts are cached.
2. **The pair list is the confirmation labeling set** (`s0c_pool.py`), not
   `s0_label_r3.build_pairs`'s development one. Order: every topic's ``pooled`` documents
   in topic order, then every topic's ``sample`` documents — Stage 0's own ordering, so
   ``pair_index`` (and therefore the presentation seed) is defined the same way.

**Quarantine.** The judge sees a topic and a document. It never sees a ranking, a score, an
arm, a mode or a budget — the pair list carries ids only (`s0c_pool.py`). Nothing here
computes, prints or logs an endpoint value; the progress file carries counts, wall-clock
and a projection.

Usage::

    python3 s0c_label.py --prepare-tokens
    python3 s0c_label.py --judge scout --limit 20 --presentations 2 --tag smoke
    python3 s0c_label.py --judge scout                 # presentations 0..19
    python3 s0c_label.py --judge qwen                  # presentations 0..9
    python3 s0c_label.py --status
    python3 s0c_label.py --merge-manifest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import s0c_common as Q          # noqa: I001
import s0_common as C
from s0_label import segment
from s0_label_r3 import CONC, JUDGES, SYSTEM, TEMPERATURE, WINDOW_TOKENS, Judge, render_r3
import s0_label_r31 as R31M

# Per-judge in-flight cap. §6.4 rule 5's "≤ 4 per endpoint" is a shared-host courtesy, not a
# server limit: mango:8004's own metrics on 2026-09-08 (12,006 requests) showed 4 running /
# 0 waiting / KV cache 1 % used (5,333 blocks × 2,096 tokens) and 11 requests per minute —
# this labeler alone — with 15.5 of every 16 s spent decoding ~2,400 thinking tokens per
# request at ~140 tok/s per stream. Batch decode would scale near-linearly there — but a
# restart at 16 in flight (2026-09-08 11:02Z) showed the SERVER admits exactly 4 sequences
# (`num_requests_running` pinned at 4.0, `num_requests_waiting` 12.0 for minutes; vLLM's
# `--max-num-seqs`). Above ~5 the surplus only queues server-side, in front of any tenant
# request, and buys nothing: 13.5 vs 11 requests/min. So Qwen runs at 5 (batch full, queue
# depth ≤ 1) until mango's admin raises `max-num-seqs`; the cap here is what the labeler
# would be allowed once that happens. Scout stays at 4 (mango:8003 was not measured).
# Concurrency is not part of the pre-registered instrument (prompt, rubric, temperature 0,
# seed) and is recorded in the manifest. Owner's decision 2026-09-08.
CONC_MAX = {"scout": CONC, "qwen": 16}

PROMPT = R31M.PROMPT
REPROMPT = R31M.REPROMPT
FAIL_PROBLEMS = R31M.FAIL_PROBLEMS
parse_and_verify_r31 = R31M.parse_and_verify_r31
order_for = R31M.order_for
presentation_range = R31M.presentation_range

RUBRIC = Q.HERE.parent / "design" / "RUBRIC-evidence.md"
N_PRESENTATIONS = {"scout": 20, "qwen": 10}     # #512's measured mixture — do not change
TOKENS = Q.GENTOK / "unit_tokens.json"
PROGRESS_EVERY = 25


def pinned_prompt_sha() -> str:
    """The prompt digest the committed r3.1 manifest records. Asserted, never recomputed."""
    m = json.loads((Q.HERE / "artifacts" / "r31ext" /
                    "label-manifest-r31.json").read_text())
    return m["prompt_sha256"]


def assert_instrument() -> dict:
    """Byte-for-byte identity of the labeling instrument with #507 / #512's."""
    man = json.loads((Q.HERE / "artifacts" / "r31ext" /
                      "label-manifest-r31.json").read_text())
    got = {"prompt_sha256": C.sha256_text(PROMPT),
           "system_sha256": C.sha256_text(SYSTEM),
           "reprompt_sha256": C.sha256_text(REPROMPT),
           "rubric_sha256": C.sha256_file(RUBRIC)}
    for k, v in got.items():
        assert man[k] == v, (
            f"{k} differs from the committed r3.1 instrument: {v} != {man[k]} — STOP. "
            "The mixture whose reliability 0.92 was measured is the mixture that must run.")
    got["temperature"] = TEMPERATURE
    got["window_tokens"] = WINDOW_TOKENS
    got["presentation_seed_formula"] = "SEED_LABELDUP + 100*k + pair_index"
    got["asserted_against"] = "artifacts/r31ext/label-manifest-r31.json"
    return got


# ---------------------------------------------------------------- the pair list
def build_pairs() -> tuple[list[tuple[str, str, str]], dict]:
    lset = json.loads((Q.POOL / "labeling_set.json").read_text())
    topics = Q.conf_topics()
    assert sorted(lset) == sorted(topics), "labeling_set is not the 80 confirmation topics"
    pairs = [(t, d, "pooled") for t in topics for d in lset[t]["pooled"]]
    pairs += [(t, d, "sample") for t in topics for d in lset[t]["sample"]]
    Q.assert_conf_only([t for t, _d, _k in pairs])
    Q.assert_no_dev([t for t, _d, _k in pairs])
    return pairs, lset


# ---------------------------------------------------------------- §6.5 window
def prepare_tokens() -> dict:
    """Per-unit generator-token counts for every document in the labeling set.

    ``mango:8003/tokenize``, ≤ 4 in flight, once, shared by both judges. Documents whose
    whole text is ≤ ``WINDOW_TOKENS`` characters are recorded as a single window without a
    call: a token is at least one character, so such a document cannot split.
    """
    t0 = time.time()
    pairs, _lset = build_pairs()
    docnos = sorted({d for _t, d, _k in pairs})
    want = set(docnos)
    docs, units = {}, {}
    for line in open(C.WORK / "docs.jsonl"):
        r = json.loads(line)
        if r["docno"] in want:
            docs[r["docno"]] = r["text"]
    for line in open(C.WORK / "units.jsonl"):
        r = json.loads(line)
        if r["docno"] in docs:
            units[r["docno"]] = r["units"]

    cache: dict = json.loads(TOKENS.read_text()) if TOKENS.exists() else {}
    short = [d for d in docnos if len(docs[d]) <= WINDOW_TOKENS]
    for d in short:
        cache.setdefault(d, {"single_window": True, "n_units": len(units[d]),
                             "chars": len(docs[d]), "counts": None})
    todo = [d for d in docnos
            if d not in cache or (not cache[d]["single_window"]
                                  and cache[d].get("counts") is None)]
    print(f"documents: {len(docnos)}  single-window (no call): {len(short)}  "
          f"to tokenize: {len(todo)}", flush=True)

    gt = C.GenTokenizer()
    lock = threading.Lock()
    n = [0]
    calls = [0]

    def one(d):
        text, us = docs[d], units[d]
        counts = []
        for u in us:
            counts.append(gt.count(text[u["start_char"]:u["end_char"]]))
        with lock:
            cache[d] = {"single_window": False, "n_units": len(us),
                        "chars": len(text), "counts": counts}
            calls[0] += len(counts)
            n[0] += 1
            if n[0] % 25 == 0:
                el = time.time() - t0
                print(f"  {n[0]}/{len(todo)} docs  {calls[0]} unit calls  {el:.0f}s",
                      flush=True)
                Q.atomic_json(TOKENS, cache)

    with ThreadPoolExecutor(CONC) as pool:
        list(pool.map(one, todo))
    Q.atomic_json(TOKENS, cache)
    out = {"documents": len(docnos), "single_window_no_call": len(short),
           "tokenized": len(todo), "unit_calls": calls[0],
           "endpoint": C.MANGO, "concurrency": CONC,
           "add_special_tokens": C.GenTokenizer.ADD_SPECIAL,
           "window_tokens": WINDOW_TOKENS,
           "seconds": round(time.time() - t0, 1), "path": str(TOKENS)}
    Q.atomic_json(Q.GENTOK / "unit_tokens_meta.json", {**out, "provenance": Q.provenance()})
    print(json.dumps(out, indent=1), flush=True)
    return out


def groups_for(docno: str, order: list[int], cache: dict) -> list[list[int]]:
    """`s0_label_r31`'s windowing loop, over precomputed counts."""
    ent = cache[docno]
    if ent["single_window"]:
        return [list(order)]
    counts = ent["counts"]
    groups, cur, curtok = [], [], 0
    for j in order:
        n = counts[j]
        if cur and curtok + n > WINDOW_TOKENS:
            groups.append(cur)
            cur, curtok = [], 0
        cur.append(j)
        curtok += n
    if cur:
        groups.append(cur)
    return groups


# ---------------------------------------------------------------- the run
def progress_path(judge: str, tag: str = "") -> pathlib.Path:
    return Q.RUN / f"progress-{judge}{('-' + tag) if tag else ''}.json"


def write_progress(judge: str, tag: str, done: int, total: int, t0: float,
                   started: str, extra: dict | None = None) -> None:
    el = time.time() - t0
    rate = done / el if (done and el > 0) else None
    Q.atomic_json(progress_path(judge, tag), {
        "judge": judge, "tag": tag or None,
        "records_done_total": done + (extra or {}).get("preexisting", 0),
        "records_this_process": done,
        "records_target_total": total,
        "records_remaining": max(total - done - (extra or {}).get("preexisting", 0), 0),
        "seconds_per_record_this_process": round(1 / rate, 3) if rate else None,
        "elapsed_seconds_this_process": round(el, 1),
        "projected_remaining_hours": (
            round(max(total - done - (extra or {}).get("preexisting", 0), 0) / rate / 3600,
                  2) if rate else None),
        "process_started_utc": started,
        "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "QUARANTINE": "counts and wall-clock only; no label content, no metric",
        **(extra or {})})


def _truncate_partial_last_line(pth) -> None:
    """A stop (SIGTERM from the supervisor) can land between a record's write and its
    newline. The writer appends, so a partial last line would be completed by the NEXT
    record and corrupt two of them. Drop the partial line before resuming; the record it
    belonged to is simply re-run (it is not in `done`)."""
    if not pth.exists() or pth.stat().st_size == 0:
        return
    blob = pth.read_bytes()
    if blob.endswith(b"\n"):
        return
    cut = blob.rfind(b"\n") + 1
    with open(pth, "rb+") as f:
        f.seek(cut)
        f.truncate()
    print(f"resume: dropped a partial last line from {pth.name} ({len(blob) - cut} bytes)",
          flush=True)


def run_judge(judge: str, p_start: int, p_end: int, limit: int, tag: str,
              conc: int) -> dict:
    inst = assert_instrument()
    print(f"instrument asserted: prompt {inst['prompt_sha256'][:16]}… "
          f"rubric {inst['rubric_sha256'][:16]}…", flush=True)
    cap = CONC_MAX.get(judge, CONC)
    if conc > cap:
        raise SystemExit(f"concurrency {conc} > {cap} for {judge} — mango is a shared host")
    assert TOKENS.exists(), "run --prepare-tokens first"
    cache = json.loads(TOKENS.read_text())

    pairs, _lset = build_pairs()
    idx = list(range(len(pairs)))
    if limit:
        idx = idx[:limit]
    # presentation-major: a run stopped early still has COMPLETE presentations for every
    # pair, which is what the pooled-reading estimator needs.
    todo = [(i, k) for k in range(p_start, p_end) for i in idx]

    suffix = f"-{tag}" if tag else ""
    out_path = Q.LABELS / f"labels-conf-{judge}{suffix}.jsonl"
    raw_path = Q.LABELS / f"raw-conf-{judge}{suffix}.jsonl"
    man_path = Q.LABELS / f"label-manifest-conf-{judge}{suffix}.json"

    done: set[tuple[str, str, int]] = set()
    for pth in (out_path, raw_path):
        _truncate_partial_last_line(pth)
    if out_path.exists():
        with open(out_path) as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    done.add((r["topic"], r["docno"], r["presentation"]))
    total = len(todo)
    todo = [(i, k) for i, k in todo if (pairs[i][0], pairs[i][1], k) not in done]
    print(f"judge={judge} pairs={len(idx)} presentations={p_start}..{p_end - 1} "
          f"records_target={total} already_done={len(done)} to_run={len(todo)}", flush=True)

    docs, units = {}, {}
    want = {pairs[i][1] for i in idx}
    for line in open(C.WORK / "docs.jsonl"):
        r = json.loads(line)
        if r["docno"] in want:
            docs[r["docno"]] = r["text"]
    for line in open(C.WORK / "units.jsonl"):
        r = json.loads(line)
        if r["docno"] in want:
            units[r["docno"]] = r["units"]
    tops = json.loads((C.CDS / "topics_merged.json").read_text())
    qrels = json.loads((C.WORK / "qrels_all.json").read_text())

    jd = Judge(judge, conc=conc)
    prompt_sha = C.sha256_text(PROMPT)
    print(f"served_model={jd.model} prompt_sha256={prompt_sha}", flush=True)

    lock = threading.Lock()
    t0 = time.time()
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0))
    fout = open(out_path, "a")
    fraw = open(raw_path, "a")
    n_done = [0]
    write_progress(judge, tag, 0, total, t0, started,
                   {"preexisting": len(done), "state": "running"})

    def one(ik):
        i, k = ik
        t, d, kind = pairs[i]
        text, us = docs[d], units[d]
        seg = segment(text, us)
        f = tops[t]["fields"]
        order, seed, order_kind = order_for(k, i, len(seg))
        groups = groups_for(d, order, cache)

        allsets, problems, raws, rawfull, finishes = [], [], [], [], []
        vstats: dict[str, int] = {}
        for g in groups:
            p = PROMPT.format(ntype=tops[t]["type"], summary=f["summary"],
                              description=f["description"], body=render_r3(seg, g))
            r = jd.chat(p)
            raws.append(r["text"])
            rawfull.append({"content": r["raw"], "reasoning": r.get("reasoning", "")})
            finishes.append(r["finish"])
            sets, probs, stx = parse_and_verify_r31(r["text"], seg, text)
            probs_all = list(probs)
            failed = any(x in FAIL_PROBLEMS or x.startswith("bad_json") for x in probs)
            if failed:                                  # §6.4 rule 2: exactly ONE retry
                r2 = jd.chat(p + REPROMPT)
                raws.append(r2["text"])
                rawfull.append({"content": r2["raw"],
                                "reasoning": r2.get("reasoning", "")})
                finishes.append(r2["finish"])
                sets2, probs2, stx2 = parse_and_verify_r31(r2["text"], seg, text)
                for k_, v_ in stx2.items():
                    stx[k_] = stx.get(k_, 0) + v_
                probs_all += list(probs2) + ["reprompted"]
                sets = sets2 if sets2 else sets
            allsets.extend(sets)
            problems.extend(probs_all)
            for k_, v_ in stx.items():
                vstats[k_] = vstats.get(k_, 0) + v_

        rec = {"topic": t, "docno": d, "kind": kind, "grade": qrels[t].get(d, 0),
               "sets": allsets, "problems": problems, "windowed": len(groups) > 1,
               "vstats": vstats, "raws": raws,
               "n_quote_fail": vstats.get("hallucinated", 0),
               "n_spans": sum(len(s["spans"]) for s in allsets),
               "n_units": len(seg), "doc_chars": len(text),
               "dropped": bool(problems) and not allsets,
               "judge": judge, "served_model": jd.model, "prompt_sha256": prompt_sha,
               "raw_response_sha256": hashlib.sha256(
                   "\n\x00\n".join(x["content"] for x in rawfull).encode()).hexdigest(),
               "finish_reasons": finishes, "pair_index": i,
               "presentation": k, "unit_order_seed": seed, "unit_order": order_kind,
               "population": "cds_conf"}
        with lock:
            # raw FIRST, then the label: a stop between the two then leaves a duplicate raw entry
            # (harmless, keyed) rather than a label whose raw is missing forever (the resume skips
            # records already in `done`, so it would never be re-fetched).
            fraw.write(json.dumps({"topic": t, "docno": d, "presentation": k,
                                   "raws": rawfull}) + "\n")
            fraw.flush()
            fout.write(json.dumps(rec) + "\n")
            fout.flush()
            n_done[0] += 1
            if n_done[0] % PROGRESS_EVERY == 0:
                el = time.time() - t0
                print(f"  {n_done[0]}/{len(todo)}  {el:.0f}s  "
                      f"{el / n_done[0]:.2f}s/record", flush=True)
                write_progress(judge, tag, n_done[0], total, t0, started,
                               {"preexisting": len(done), "state": "running",
                                "llm": jd.stats()})
        return None

    list(jd.pool.map(one, todo))
    fout.close()
    fraw.close()
    wall = round(time.time() - t0, 1)
    n_recs = sum(1 for line in open(out_path) if line.strip())
    man = {
        "judge": judge,
        "population": "cds_conf (the 80 confirmation topics)",
        "protocol": ("SPEC-confirmation-run-r3.md §3.7 item 1 (whole-sentence anchors) at "
                     "the depth #512 measured usable: scout x20 + qwen x10 = 30 pooled "
                     "readings"),
        "instrument": assert_instrument(),
        "stats": jd.stats(), "n_pairs_total": len(idx),
        "concurrency": conc, "concurrency_cap": CONC_MAX.get(judge, CONC),
        "presentations_run": [p_start, p_end],
        "n_records_target": total, "n_records_total": n_recs,
        "n_records_run": len(todo), "n_records_preexisting": len(done),
        "labels_path": str(out_path), "raw_path": str(raw_path),
        "window_tokens": WINDOW_TOKENS,
        "window_counts_precomputed": str(TOKENS),
        "started_utc": started,
        "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "wall_seconds": wall,
        "seconds_per_record": round(wall / max(len(todo), 1), 3),
        "QUARANTINE": "labels written, never summarised; no metric is computed here",
        "provenance": Q.provenance()}
    Q.atomic_json(man_path, man)
    write_progress(judge, tag, n_done[0], total, t0, started,
                   {"preexisting": len(done), "state": "finished", "llm": jd.stats()})
    print(f"labels written: {out_path} (+{len(todo)} this run) wall={wall}s",
          json.dumps(jd.stats()), flush=True)
    return man


WORKTREE_RUN = Q.HERE.parents[3] / "run"    # where the supervisor records its pids


def status() -> dict:
    """Counts, liveness and projections. Never a label, never a metric."""
    out: dict = {"progress_files": {}, "heartbeats": {}, "labels": {}, "pids": {}}
    for p in sorted(Q.RUN.glob("progress-*.json")):
        out["progress_files"][p.name] = json.loads(p.read_text())
    for p in sorted(Q.RUN.glob("heartbeat-*.json")):
        try:
            out["heartbeats"][p.name] = json.loads(p.read_text())
        except json.JSONDecodeError:
            out["heartbeats"][p.name] = "unreadable (mid-write)"
    for p in sorted(Q.LABELS.glob("labels-conf-*.jsonl")):
        out["labels"][p.name] = {"bytes": p.stat().st_size,
                                 "records": sum(1 for line in open(p) if line.strip())}
    for d in (WORKTREE_RUN, Q.RUN):
        for p in sorted(d.glob("*.pid")):
            pid = p.read_text().strip()
            alive = pathlib.Path(f"/proc/{pid}").exists()
            cwd = None
            try:
                cwd = str(pathlib.Path(f"/proc/{pid}/cwd").resolve())
            except OSError:
                pass
            out["pids"][f"{d.name}/{p.name}"] = {"pid": pid, "alive": alive, "cwd": cwd}
    return out


def merge_manifest() -> pathlib.Path:
    per = {}
    for j in sorted(JUDGES):
        p = Q.LABELS / f"label-manifest-conf-{j}.json"
        if p.exists():
            per[j] = json.loads(p.read_text())
    smoke = {}
    for j in sorted(JUDGES):
        p = Q.LABELS / f"label-manifest-conf-{j}-smoke.json"
        if p.exists():
            smoke[j] = json.loads(p.read_text())
    out = {"population": "cds_conf", "instrument": assert_instrument(),
           "n_presentations_per_judge": N_PRESENTATIONS,
           "judges": {j: {k: m.get(k) for k in
                          ("stats", "n_pairs_total", "n_records_total", "n_records_target",
                           "presentations_run", "wall_seconds", "seconds_per_record",
                           "started_utc", "finished_utc")}
                      for j, m in per.items()},
           "smoke": {j: {k: m.get(k) for k in
                         ("stats", "n_records_total", "wall_seconds",
                          "seconds_per_record")}
                     for j, m in smoke.items()},
           "endpoints_contacted": [JUDGES[j]["base"] for j in sorted(JUDGES)] + [C.MANGO],
           "stores_contacted": "none",
           "QUARANTINE": "counts only"}
    Q.atomic_json(Q.LABELS / "label-manifest-conf.json", out)
    Q.atomic_json(Q.ART / "label-manifest-conf.json", out)
    return Q.LABELS / "label-manifest-conf.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prepare-tokens", action="store_true")
    ap.add_argument("--judge", choices=sorted(JUDGES))
    ap.add_argument("--presentations", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tag", default="")
    ap.add_argument("--conc", type=int, default=CONC)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--merge-manifest", action="store_true")
    ap.add_argument("--assert-instrument", action="store_true")
    args = ap.parse_args()

    if args.assert_instrument:
        print(json.dumps(assert_instrument(), indent=1))
        return
    if args.prepare_tokens:
        prepare_tokens()
        return
    if args.status:
        print(json.dumps(status(), indent=1))
        return
    if args.merge_manifest:
        print(merge_manifest())
        return
    if not args.judge:
        raise SystemExit("--judge is required (or --prepare-tokens / --status / "
                         "--merge-manifest / --assert-instrument)")
    spec = args.presentations or str(N_PRESENTATIONS[args.judge])
    p_start, p_end = presentation_range(spec)
    run_judge(args.judge, p_start, p_end, args.limit, args.tag, args.conc)


if __name__ == "__main__":
    sys.exit(main())
