#!/usr/bin/env python
"""Batch driver for GoWe-run ingest workflows (#301): submit → verify → clean → next.

Runs a shard plan (``plan_shards.py`` output) through ``cwl/jats-ingest.cwl`` in
bounded batches instead of one giant submission. Three measured reasons, each of
which alone would justify it:

* **Pipelining.** One submission does all embeds and THEN one load; batches let
  batch N's load run while batch N+1 embeds, turning sum into max.
* **Disk.** The full harvest's intermediate embedding files total ~1.6 TB against
  a stage-out filesystem with far less free; a 64-shard batch peaks near ~50 GB
  and is cleaned as soon as its load confirms.
* **Blast radius.** A batch is the retry unit: its load summary either says all
  shards loaded or names the failures, and point ids are deterministic
  (uuid5(tenant:chunk_id), #303), so re-running a batch upserts idempotently.

The driver keeps a **ledger** (JSONL, one row per batch attempt) in the output
directory and is resumable: rerunning skips batches whose row says ``done``. A
FAILED batch stops the driver (``--continue-on-failure`` to keep going); rerun
after fixing to retry just that batch.

**Verification is against the stores, not the exit code.** After each batch the
driver requires: submission COMPLETED; a load summary with ``n_shards_failed==0``
staged after the batch started; Qdrant and ES agreeing with each other; and the
count advancing by the summary's ``n_chunks`` for new work (a re-run batch
legitimately advances by less — down to 0 — which is idempotency, not loss, so
the delta is recorded and only leg *disagreement* is fatal).

**Cleanup deletes only ``*.emb.jsonl``** staged for this batch — the vectors are
in the store once the load confirms; receipts, skip reports and summaries are
kept. ``--keep-embeddings`` disables it.

Assumes it is the only submitter of this workflow against the stage-out dir
while running (it maps outputs to batches by mtime window).

Usage::

    python scripts/gowe_batch_ingest.py \
        --plan /rag/ingest/oa/r1 \
        --cwl cwl/jats-ingest.cwl \
        --inputs-template /rag/ingest/oa/jats-ingest.base.yml \
        --out /rag/ingest/oa/r1-run \
        --batch-size 64 --dry-run     # then drop --dry-run
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

TERMINAL = ("COMPLETED", "FAILED", "CANCELLED", "ERROR")


# --------------------------------------------------------------------------- #
# gowe CLI
# --------------------------------------------------------------------------- #

def submit(gowe: str, server: str, cwl: str, inputs: str, group: str) -> str:
    """Submit one batch; return the submission id."""
    cmd = [gowe, "submit", cwl, "-i", inputs, "--server", server,
           "--group", group, "--no-upload"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    m = re.search(r"Submission created:\s+(\S+)", r.stdout + r.stderr)
    if r.returncode != 0 or not m:
        raise RuntimeError(
            f"gowe submit failed (rc={r.returncode}):\n{r.stdout[-1000:]}\n{r.stderr[-1000:]}"
        )
    return m.group(1)


def poll(gowe: str, server: str, sub_id: str, *, interval: float, timeout: float) -> str:
    """Poll until the submission reaches a terminal state; return that state."""
    deadline = time.monotonic() + timeout
    while True:
        r = subprocess.run([gowe, "status", sub_id, "--server", server],
                           capture_output=True, text=True, timeout=120)
        m = re.search(r"State:\s+(\S+)", r.stdout)
        state = m.group(1) if m else "UNKNOWN"
        if state in TERMINAL:
            return state
        if time.monotonic() > deadline:
            return f"TIMEOUT({state})"
        time.sleep(interval)


# --------------------------------------------------------------------------- #
# store verification
# --------------------------------------------------------------------------- #

def _get_json(url: str, payload: dict | None = None) -> Any:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def store_counts(qdrant_url: str, es_url: str, store: str) -> tuple[int, int]:
    """Both legs' counts. A 404 is 0, not an error: batch 0 of a fresh
    collection runs before the load's ensure_collection() has created the
    physical store — the registry entry exists (#263 requires it), the bytes
    don't yet."""
    import urllib.error

    def _count(url: str) -> int:
        try:
            d = _get_json(url)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 0
            raise
        if "result" in d:
            return int(d["result"]["points_count"])
        return int(d["count"])

    return (_count(f"{qdrant_url.rstrip('/')}/collections/{store}"),
            _count(f"{es_url.rstrip('/')}/{store}/_count"))


def settled_store_counts(qdrant_url: str, es_url: str, store: str,
                         max_wait: float = 600.0, interval: float = 1.0,
                         stable_rounds: int = 3) -> tuple[int, int]:
    """Both legs' counts once they AGREE, or the last read at the deadline.

    A single immediate read races the stores, and that race became likely after
    two changes that were individually right:

    * the legs are now written CONCURRENTLY, so neither finishes last by
      construction — the old serial order (vectors, then text) meant the vector
      store was long done whenever the loader returned;
    * the text index has its refresh parked during a bulk load, so its count
      only becomes visible at the explicit refresh on the way out.

    The vector store also acknowledges an upsert before the points are fully
    applied (the collection reports ``yellow`` while it catches up), so a count
    taken the instant the workflow reports COMPLETED can be short by a
    five-figure number and recover within a minute. That is exactly what
    happened on 01024-01087: a 16,950 disagreement that converged to zero in
    ~60 s — after the driver had already written `failed` and triggered a
    full re-run of a batch that was fine.

    Poll instead, forcing a text-index refresh each round so a parked refresh
    interval cannot make the count lie. Two properties keep this cheap in both
    directions:

    * **Stability, not just time, ends the wait.** A settling store is *moving*;
      a real gap is static. Once both legs read identically ``stable_rounds``
      times in a row and still disagree, waiting longer cannot help — fail then,
      not at the deadline. A genuinely broken batch fails in seconds rather than
      sitting out the whole window.
    * **The interval backs off** from ``interval`` up to 30 s, so the common case
      (converges almost immediately) costs about a second, while a slow apply on
      a large collection still gets the full window without hammering the stores.
    """
    import time
    import urllib.request

    deadline = time.monotonic() + max_wait
    q, e = store_counts(qdrant_url, es_url, store)
    wait, stable, prev = interval, 0, (q, e)
    while q != e and time.monotonic() < deadline:
        time.sleep(wait)
        wait = min(wait * 2, 30.0)
        try:  # best-effort: a refresh failure must not fail the verification
            urllib.request.urlopen(
                urllib.request.Request(
                    f"{es_url.rstrip('/')}/{store}/_refresh", method="POST"),
                timeout=60,
            ).close()
        except Exception:  # noqa: BLE001
            pass
        q, e = store_counts(qdrant_url, es_url, store)
        stable = stable + 1 if (q, e) == prev else 0
        prev = (q, e)
        if stable >= stable_rounds:
            break  # neither leg is moving: this is a real gap, not a settle
    return q, e


def resolve_store_name(registry_db: str, collection_id: str) -> str:
    """The physical store name from the registry entry — the driver verifies
    counts against the same store the workflow writes, resolved the same way."""
    import sqlite3

    with sqlite3.connect(f"file:{registry_db}?mode=ro", uri=True) as conn:
        row = conn.execute(
            "SELECT collection FROM collections WHERE id = ?", (collection_id,)
        ).fetchone()
    if not row:
        raise SystemExit(f"collection {collection_id!r} not in registry {registry_db}"
                         " — register it via POST /v1/collections first (#263)")
    return str(row[0])


# --------------------------------------------------------------------------- #
# stage-out mapping (by mtime window; see module docstring's single-submitter note)
# --------------------------------------------------------------------------- #

def staged_since(stage_dir: str, pattern: str, t0: float) -> list[str]:
    out = []
    for p in glob.glob(os.path.join(stage_dir, "*", pattern)):
        try:
            if os.path.getmtime(p) >= t0:
                out.append(p)
        except OSError:
            continue
    return sorted(out, key=os.path.getmtime)


def read_load_summary(stage_dir: str, t0: float) -> dict | None:
    paths = staged_since(stage_dir, "load-summary.json", t0)
    if not paths:
        return None
    with open(paths[-1], encoding="utf-8") as fh:  # newest = this batch's
        return json.load(fh)


def clean_embeddings(stage_dir: str, t0: float, *, dry: bool) -> int:
    """Delete this batch's staged ``*.emb.jsonl``. Returns bytes reclaimed."""
    total = 0
    for p in staged_since(stage_dir, "*.emb.jsonl", t0):
        total += os.path.getsize(p)
        if not dry:
            os.unlink(p)
    return total


# --------------------------------------------------------------------------- #
# ledger
# --------------------------------------------------------------------------- #

def read_ledger(path: str) -> dict[str, dict]:
    """Latest row per batch key. Append-only file; last write wins."""
    out: dict[str, dict] = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            out[row["batch"]] = row
    return out


def append_ledger(path: str, row: dict) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


# --------------------------------------------------------------------------- #
# batches
# --------------------------------------------------------------------------- #

def make_batches(plan_dir: str, batch_size: int) -> list[tuple[str, list[str]]]:
    """``[(batch_key, [shard paths])]`` — consecutive sorted shard files, so a
    batch key names the same shards on every run (resume depends on it)."""
    shards = sorted(glob.glob(os.path.join(plan_dir, "shard-*.jsonl")))
    if not shards:
        raise SystemExit(f"{plan_dir}: no shard-*.jsonl (is this a plan_shards.py output dir?)")
    batches = []
    for i in range(0, len(shards), batch_size):
        chunk = shards[i:i + batch_size]
        first = Path(chunk[0]).stem.split("-")[1]
        last = Path(chunk[-1]).stem.split("-")[1]
        batches.append((f"{first}-{last}", chunk))
    return batches


def render_inputs(template: dict, shard_paths: list[str], out_path: str) -> None:
    doc = dict(template)
    doc["shards"] = [{"class": "File", "location": p} for p in shard_paths]
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)  # JSON is valid YAML; no dependency needed


# --------------------------------------------------------------------------- #
# main loop
# --------------------------------------------------------------------------- #

def _last_status(ledger_path: str, batch: str) -> str | None:
    """The batch's most recent recorded status ('done' | 'failed' | 'timeout')."""
    return read_ledger(ledger_path).get(batch, {}).get("status")


def run_batch(args, template: dict, store: str, key: str,
              shard_paths: list[str], ledger_path: str,
              reattach_sub: str | None = None) -> bool:
    """One batch through submit → poll → verify → clean. True = batch done.

    ``reattach_sub`` skips the submit and re-polls an EXISTING submission — the
    recovery for a driver that timed out (or died) while its submission kept
    running. Without it, a rerun resubmits and redoes hours of embedding that
    the still-running submission already owns. Found live on the OA pilot: the
    driver declared TIMEOUT and stopped while the submission was mid-load.
    """
    t0 = time.time()
    q0, e0 = store_counts(args.qdrant_url, args.es_url, store)
    if reattach_sub:
        sub_id = reattach_sub
        print(f"[{key}] re-attaching to {sub_id}", flush=True)
    else:
        inputs_path = os.path.join(args.out, f"inputs-{key}.json")
        render_inputs(template, shard_paths, inputs_path)
        sub_id = submit(args.gowe_bin, args.server, args.cwl, inputs_path, args.group)
        print(f"[{key}] submitted {sub_id} ({len(shard_paths)} shards)", flush=True)
    state = poll(args.gowe_bin, args.server, sub_id,
                 interval=args.poll_interval, timeout=args.batch_timeout)

    row = {"batch": key, "shards": [os.path.basename(p) for p in shard_paths],
           "submission": sub_id, "state": state, "started": t0,
           "qdrant_before": q0, "es_before": e0}

    ok = state == "COMPLETED"
    summary = read_load_summary(args.stage_out, t0) if ok else None
    if ok and summary is None:
        ok, row["error"] = False, "COMPLETED but no load-summary.json staged after t0"
    if ok and summary is not None and summary.get("n_shards_failed"):
        ok, row["error"] = False, f"load summary reports failed shards: {summary}"

    # Settle before judging: the legs are written concurrently and the vector
    # store applies upserts asynchronously, so an immediate read races them.
    q1, e1 = settled_store_counts(args.qdrant_url, args.es_url, store,
                                  max_wait=args.settle_timeout)
    row.update(qdrant_after=q1, es_after=e1,
               loaded_chunks=(summary or {}).get("n_chunks"),
               delta=q1 - q0)
    if ok and q1 != e1:
        # Leg disagreement is data loss in one store; a small/zero DELTA is not
        # (an idempotent re-run legitimately advances by less than n_chunks).
        # Surviving the settle window means it did not converge, so it is real.
        ok, row["error"] = False, (
            f"legs disagree after {args.settle_timeout:.0f}s settle: "
            f"qdrant={q1} es={e1}")

    if ok and not args.keep_embeddings:
        row["reclaimed_bytes"] = clean_embeddings(args.stage_out, t0, dry=False)

    # A timeout is NOT a failure: the submission is (very likely) still
    # running server-side, and a rerun must re-attach to it, not resubmit.
    if state.startswith("TIMEOUT"):
        row["status"] = "timeout"
    else:
        row["status"] = "done" if ok else "failed"
    row["wall_s"] = round(time.time() - t0, 1)
    append_ledger(ledger_path, row)
    print(f"[{key}] {row['status']}: state={state} delta={row['delta']} "
          f"legs={q1}/{e1} wall={row['wall_s']}s"
          + (f" ERROR: {row.get('error')}" if not ok else ""), flush=True)
    return ok


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--plan", required=True, help="plan_shards.py output dir")
    p.add_argument("--cwl", required=True, help="workflow to submit (jats-ingest.cwl)")
    p.add_argument("--inputs-template", required=True,
                   help="inputs YAML/JSON with everything EXCEPT shards; the driver "
                        "fills shards per batch")
    p.add_argument("--out", required=True,
                   help="driver state dir: ledger + rendered per-batch inputs")
    p.add_argument("--batch-size", type=int, default=64)
    p.add_argument("--limit-batches", type=int, default=0, help="run at most N batches (0 = all)")
    p.add_argument("--gowe-bin", default="gowe")
    p.add_argument("--server", default="http://localhost:8091")
    p.add_argument("--group", default="ragstack")
    p.add_argument("--stage-out", default="/scout/wf/data",
                   help="workers' --stage-out dir (for load summaries + cleanup)")
    p.add_argument("--keep-embeddings", action="store_true",
                   help="do not delete staged *.emb.jsonl after a verified load")
    p.add_argument("--poll-interval", type=float, default=20.0)
    p.add_argument("--batch-timeout", type=float, default=12 * 3600,
                   help="seconds before a batch is declared TIMEOUT (default 12h). "
                        "A timeout row keeps its submission id and a rerun "
                        "RE-ATTACHES to it instead of resubmitting.")
    p.add_argument("--settle-timeout", type=float, default=600.0,
                   help="seconds to let the two legs converge before calling a "
                        "count difference a real disagreement (default 600). The "
                        "legs are written concurrently and the vector store "
                        "applies upserts asynchronously, so an immediate read "
                        "races them: a 16,950 gap on one batch converged to zero "
                        "in ~60s, after the driver had already failed it and "
                        "re-run a healthy batch. A genuine disagreement never "
                        "converges, so this only delays true failures.")
    p.add_argument("--continue-on-failure", action="store_true")
    p.add_argument("--retries", type=int, default=1,
                   help="resubmit a FAILED batch this many times before stopping "
                        "(default 1). Infrastructure faults are real: a batch died "
                        "after 128/130 tasks succeeded because apptainer could not "
                        "resolve the run-as uid for ~5 s (transient LDAP), and its "
                        "three in-worker retries all fell inside that window. "
                        "Timeouts are NOT retried here — they re-attach instead.")
    p.add_argument("--dry-run", action="store_true",
                   help="print the batch plan and per-batch shard counts; submit nothing")
    args = p.parse_args(argv)

    import yaml  # runtime dep of the driver only; the worker image doesn't need it

    with open(args.inputs_template, encoding="utf-8") as fh:
        template = yaml.safe_load(fh)
    if "shards" in template:
        # A template that already lists shards would silently pin every batch to
        # the same files — the exact class of mistake a driver exists to prevent.
        raise SystemExit("--inputs-template must not contain 'shards'; the driver fills them")
    for req in ("collection_id", "registry_db", "qdrant_url", "es_url"):
        if req not in template:
            raise SystemExit(f"--inputs-template missing {req!r}")

    args.qdrant_url = template["qdrant_url"]
    args.es_url = template["es_url"]
    store = resolve_store_name(template["registry_db"]["location"],
                               template["collection_id"])

    batches = make_batches(args.plan, args.batch_size)
    if args.limit_batches:
        batches = batches[: args.limit_batches]

    os.makedirs(args.out, exist_ok=True)
    ledger_path = os.path.join(args.out, "ledger.jsonl")
    ledger = read_ledger(ledger_path)
    done = {k for k, r in ledger.items() if r.get("status") == "done"}
    reattach = {k: r["submission"] for k, r in ledger.items()
                if r.get("status") == "timeout" and r.get("submission")}

    todo = [(k, s) for k, s in batches if k not in done]
    n_shards = sum(len(s) for _, s in todo)
    print(f"{len(batches)} batch(es), {len(done)} already done, {len(todo)} to run "
          f"({n_shards} shards) → store {store!r}", flush=True)
    if args.dry_run:
        for k, s in todo:
            print(f"  [{k}] {len(s)} shards: {os.path.basename(s[0])} .. "
                  f"{os.path.basename(s[-1])}")
        return 0

    for k, s in todo:
        ok = run_batch(args, template, store, k, s, ledger_path,
                       reattach_sub=reattach.get(k))
        attempt = 0
        # RETRY ONLY A REAL FAILURE. A timeout means the DRIVER gave up while its
        # submission is very likely still running — resubmitting then duplicates
        # the whole batch's GPU work and races two loads into one store. Observed
        # live: batch 00448-00511 timed out at 12h with its submission at 129/130
        # tasks, load step still running, and the retry launched 64 redundant
        # embeds. Re-attach handles this case, on the next driver run.
        while (not ok and attempt < args.retries
               and _last_status(ledger_path, k) == "failed"):
            attempt += 1
            print(f"[{k}] retry {attempt}/{args.retries} after failure", flush=True)
            ok = run_batch(args, template, store, k, s, ledger_path)
        if not ok:
            if not args.continue_on_failure:
                print(f"stopping at failed batch {k} (rerun to retry it)", file=sys.stderr)
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
