#!/usr/bin/env python
"""Alias study (OA-1000): the dry run of the whole chain on NON-study records (DRAFT prereg §15
item 12), against a fake retriever and fake judges: no store, no gateway, no model, no network.

    alias_study_dryrun.py --records VALIDATION_ACCEPTED.jsonl --base-export CHUNKS.jsonl \\
                          --glossary MERGED.jsonl --work DIR [--study-records ACCEPTED.jsonl] \\
                          [--added-per-arm 60] [--batch-size 300] [--n-perm 2000] [--n-boot 2000]

What runs, unchanged, is the study's code, in the prereg's two stages (§5.7, owner decisions of
2026-11-03): ``alias_study_run.py --stage A`` (retrieval of the 19 stage-A legs: the five
Layer-1 attachments at every retriever and the four noise repeats, every gate: the load receipt,
depth read-back, boost probe ``declared``, the admin config hash, gold presence, per-leg failed
calls, the inactive-reranker gate), ``alias_study_judge.py`` on judging stage A's pairs (14
legs; registry on demand, batches, C / E / Argon / Opus drift sample with the one-retry cap,
finalize with the drift alarm, ``commit-stage-a``), then ``alias_study_run.py --stage B`` (the
other 9 cells and the placebo leg) and the judge on stage B's new pairs (the Layer-1 bm25 legs
among them), then the analysis gate, a row count of every leg, and ``alias_study_stats.py``
(Layer 1, Layer 2, sensitivities, the public check). What is fake:

* **the corpora**: ``base`` is the real chunk export (read only); each expanded arm adds
  ``--added-per-arm`` synthetic papers, copies of base chunks under new chunk ids and paper ids
  (``DRY…``), written to ``--work``;
* **the retriever** (:class:`FakeServer`, an ``httpx.MockTransport``): BM25 over the arm's
  chunks (``glossbench_alias_judge.BM25``), with a seeded perturbation per retriever and per
  glossary arm, so the cells differ; one collection id per attachment; ``/v1/config`` answers a
  fixed admin config;
* **the collections' load receipts and glossary-file hashes**, written to ``--work`` (no live
  census: a dry run has no index);
* **the judges** (:class:`FakeJudges`): a verdict from a hash of the user message, so the
  judges mostly agree, sometimes disagree (escalations), Argon sometimes answers unparseably
  (the re-issue) and Opus sometimes answers empty (filtered pairs).

**Refusals.** Every record must be a validation record (``set`` ``V``); with
``--study-records`` (the study's accepted file) any record id found there is refused, and the
dry run never reads that file's records otherwise. ``--work`` must be outside the repository.
Every output carries ``dry_run: true`` and is not citable.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE / "glossbench"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import alias_study_common as asc  # noqa: E402
import alias_study_judge as ajd  # noqa: E402
import alias_study_run as ar  # noqa: E402
import alias_study_stats as ast  # noqa: E402
import glossbench_alias_judge as jd  # noqa: E402

DRY_URL = "http://dryrun.invalid"
#: the gateway client refuses a URL without an explicit port (deployed_docs_trial.check_url)
DRY_GW = "http://dryrun.invalid:18799/v1"
CONFIG = {"retrieval": {"rerank_candidates": 50}, "dry_run": True}


def h(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:12], 16)


def stamp(glossary: str) -> str:
    return f"dry-{glossary}@file/1"


# ------------------------------------------------------------------ the fake retriever

class FakeServer:
    """``/v1/retrieve`` over the arm's chunks: BM25, perturbed per retriever and glossary arm."""

    def __init__(self, base: Mapping[str, Mapping[str, Any]], added: Mapping[str, Mapping[str, Mapping[str, Any]]],
                 collections: Mapping[str, tuple[str, str]]) -> None:
        self.index = {"base": jd.BM25([(c, r["content"]) for c, r in base.items()])}
        self.added = {a: jd.BM25([(c, r["content"]) for c, r in rows.items()]) for a, rows in added.items()}
        #: collection id -> (corpus arm, glossary arm): one read-only collection per attachment
        self.attachment_of = dict(collections)
        self.cache: dict[tuple[str, str], list[str]] = {}
        self.calls = 0

    def ranked(self, arm: str, query: str) -> list[str]:
        key = (arm, query)
        if key not in self.cache:
            scores = self.index["base"].scores(query)
            if arm in self.added:
                scores.update(self.added[arm].scores(query))
            self.cache[key] = [c for c, _ in sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:120]]
        return self.cache[key]

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/v1/version":
            return httpx.Response(200, json={"version": "dry-run"})
        if request.method == "GET" and path == "/v1/config":
            return httpx.Response(200, json=CONFIG)
        if request.method == "POST" and path == "/v1/retrieve":
            self.calls += 1
            body = json.loads(request.content)
            arm, gl = self.attachment_of[body["collection"]]
            ids = list(self.ranked(arm, body["query"]))
            mode = "bm25" if body["retrieval_mode"] == "bm25" else ("rerank" if body.get("rerank") else "rrf")
            if mode != "bm25" or gl != "none":
                salt = f"{mode}|{gl}|{body['query']}"
                ids = sorted(ids[:60], key=lambda c: (ids.index(c) + (h(salt, c) % 25))) + ids[60:]
            top = ids[: int(body["top_k"])]
            return httpx.Response(200, json={"sources": [{"chunk_id": c, "metadata": {
                "symbol_glossary_version": stamp(gl)}} for c in top]})
        return httpx.Response(404, json={"detail": "not found"})


# ------------------------------------------------------------------ fake judges

class Resp:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self.text = json.dumps(body)
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        return json.loads(self.text)


class FakeJudges:
    """One async client for every judge endpoint; the verdict is a hash of the user message."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    async def post(self, url: str, json: dict[str, Any]) -> Resp:  # noqa: A002
        model = json["model"]
        self.calls[model] = self.calls.get(model, 0) + 1
        user = json["messages"][-1]["content"]
        truth = h("truth", user) % 4 == 0
        n = int(json.get("n") or 1)
        choices = []
        for i in range(n):
            v = truth
            if model.startswith("Qwen3.6") and h("c", user, str(i)) % 9 == 0:
                v = not v
            if model.startswith("Qwen3.8") and h("e", user) % 15 == 0:
                v = not v
            text = json_verdict(v)
            if model == "Gemini 4 Argon" and h("argon", user, str(self.calls[model])) % 6 == 0:
                text = "I cannot decide."
            if model == "Claude Opus 5.5" and h("opus", user) % 10 == 0:
                text = ""
            choices.append({"index": i, "message": {"content": text}, "finish_reason": "stop"})
        await asyncio.sleep(0)
        return Resp(200, {"model": model, "choices": choices,
                          "usage": {"prompt_tokens": 300, "completion_tokens": 30 * n}})

    async def aclose(self) -> None:
        pass


def json_verdict(v: bool) -> str:
    return json.dumps({"reason": "dry run", "answers": "yes" if v else "no"})


# ------------------------------------------------------------------ the dry run

def refuse_study_records(recs: Sequence[Mapping[str, Any]], study: Path | None) -> None:
    bad = [r for r in recs if r.get("set") != "V"]
    if bad:
        raise SystemExit(f"dry run: {len(bad)} record(s) are not validation records (set V); refused")
    if study is not None:
        ids = {json.loads(x)["record_id"] for x in study.read_text(encoding="utf-8").splitlines() if x.strip()}
        clash = [r for r in recs if r["record_id"] in ids]
        if clash:
            raise SystemExit(f"dry run: {len(clash)} record id(s) are study records; refused")


def synth_added(base: Mapping[str, Mapping[str, Any]], arm: str, n_papers: int, per_paper: int = 3
                ) -> list[dict[str, Any]]:
    ids = sorted(base, key=lambda c: h("added", arm, c))[: n_papers * per_paper]
    rows = []
    for i, cid in enumerate(ids):
        paper = f"DRY{arm.upper()}{i // per_paper:04d}"
        src = base[cid]
        rows.append({"chunk_id": f"dry-{arm}-{i:05d}", "doc_id": f"doc-{paper}", "content": src["content"],
                     "start_char": 0, "end_char": len(src["content"]),
                     "metadata": {"source_path": paper, "title": f"Dry-run paper {paper}"}})
    return rows


def write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def dry_run(records: Path, base_export: Path, glossary: Path, work: Path, *, study_records: Path | None = None,
            added_per_arm: int = 60, batch_size: int = 300, n_perm: int = 2000, n_boot: int = 2000) -> dict[str, Any]:
    repo = _HERE.parents[2].resolve()
    work = work.resolve()
    if work == repo or repo in work.parents:
        raise SystemExit("--work is inside the repository; private outputs must live outside it")
    recs = asc.load_records(records)
    refuse_study_records(recs, study_records)
    work.mkdir(parents=True, exist_ok=True)
    base = asc.read_chunk_exports([base_export])
    added_rows = {a: synth_added(base, a, added_per_arm) for a in asc.CORPUS_ARMS if a != "base"}
    exports: dict[str, Path] = {}
    for a, rows in added_rows.items():
        exports[a] = work / f"added-{a}.jsonl"
        write_jsonl(exports[a], rows)
    added = {a: {r["chunk_id"]: r for r in rows} for a, rows in added_rows.items()}
    gfiles = {}
    for g in ar.STUDY_GLOSSARY_ARMS:
        gf = work / f"glossary-{g}.txt"
        gf.write_text(f"dry-run glossary arm {g}\n")
        gfiles[g] = asc.sha256_file(gf)
    expected = {a: len(base) + len(added.get(a, {})) for a in asc.CORPUS_ARMS}
    corpora: dict[str, Any] = {a: {"expected_chunks": expected[a],
                                   "chunk_export": [str(base_export)] + ([str(exports[a])] if a in exports else [])}
                               for a in asc.CORPUS_ARMS}
    # one collection per attachment, each with the load receipt its (fake) load would write
    receipts = work / "receipts"
    receipts.mkdir(exist_ok=True)
    cols: dict[str, dict[str, Any]] = {}
    attach: dict[str, tuple[str, str]] = {}
    for a in asc.CORPUS_ARMS:
        for g in ar.STUDY_GLOSSARY_ARMS:
            if g == ar.PLACEBO and a != "base":
                continue
            cid = f"bench-alias-dry-{a}-{g}"
            rp = receipts / f"{a}-{g}.json"
            rp.write_text(json.dumps({
                "n_shards": 1, "n_shards_failed": 0, "n_chunks": expected[a], "failed_shards": [], "errors": {},
                "provenance": {"workflow_id": "dry-run"},
                "glossary_file": None if g == "none" else {"symbol_glossary": {
                    "sha256": gfiles[g], "glossary_versions": [f"dry-{g}"], "counts": {}}}}))
            cols.setdefault(a, {})[g] = {"collection": cid, "index": f"ragstack_lib_bench_dry_{a}_{g}",
                                         "load_receipt": str(rp)}
            attach[cid] = (a, g)
    gold_hashes = work / "gold-hashes.json"
    gold_hashes.write_text(json.dumps({r["gold_chunks"][0]: asc.sha_text(base[r["gold_chunks"][0]]["content"])
                                       for r in recs}))
    arms: dict[str, Any] = {"none": {"stamps": ["dry-none"]}}
    for g in ("merged", ar.PLACEBO):
        arms[g] = {"stamps": [f"dry-{g}"], "glossary_file_sha256": dict.fromkeys(asc.CORPUS_ARMS, gfiles[g])}
    run_cfg = {"api": {"base_url": DRY_URL, "retry_backoff_s": 0.0}, "corpora": corpora, "collections": cols,
               "arms": arms, "records": str(records), "out_dir": str(work / "runs"), "gold_hashes": str(gold_hashes),
               "dry_run": True}
    cfgp = work / "run-config.json"
    cfgp.write_text(json.dumps(run_cfg, indent=1))
    server = FakeServer(base, added, attach)
    codes: dict[str, int] = {}

    def run_stage(stage: str) -> None:
        done: set[tuple[str, str, int]] = set()
        for c, g, _r, rep in ar.stage_legs(stage):
            if (c, g, rep) in done:
                continue
            done.add((c, g, rep))
            argv = ["--config", str(cfgp), "run", "--stage", stage, "--corpus-arm", c, "--glossary", g,
                    "--boost-declared", "1.0"] + (["--repeat", "2"] if rep == 2 else [])
            codes[f"{stage}|{c}|{g}|{rep}"] = ar.main(argv, client_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(server.handler)))
        if any(codes.values()):
            raise SystemExit(f"dry run: a leg did not complete: {codes}")

    # the judge
    jcfg = {"out_dir": str(work / "judge"), "runs_dir": str(work / "runs"), "records": str(records),
            "chunk_exports": {"base": str(base_export), **{a: str(p) for a, p in exports.items()}},
            "glossary": str(glossary), "batch_size": batch_size, "dry_run": True,
            "caps": {"argon_share": 1.0, "opus_share": 1.0},
            "judge_c": {"base_url": f"{DRY_URL}/c/v1", **{k: v for k, v in ajd.FIXED["C"].items() if k != "variant"}},
            "judge_e": {"base_url": f"{DRY_URL}/e/v1", **{k: v for k, v in ajd.FIXED["E"].items() if k != "variant"}},
            "tiebreak": {"base_url": DRY_GW, "empty_retry_s": 0.0,
                         **{k: v for k, v in ajd.FIXED["argon"].items() if k != "tag"}},
            "drift_ref": {"base_url": DRY_GW, "empty_retry_s": 0.0,
                          **{k: v for k, v in ajd.FIXED["opus"].items() if k != "tag"}}}
    jcfgp = work / "judge-config.json"
    jcfgp.write_text(json.dumps(jcfg, indent=1))
    fake = FakeJudges()
    prov = {"dry_run": True}

    def judge(*a: str) -> int:
        return ajd.main(["--config", str(jcfgp), *a], http_factory=lambda: fake, prov=prov)

    jcodes: dict[str, int] = {}

    def judge_stage(stage: str) -> None:
        assert judge("build-pairs", "--stage", stage) == 0
        nos = json.loads((work / "judge" / "batches.json").read_text())["stages"][stage]
        for b in nos:
            for be in ajd.BACKENDS:
                judge("preflight", "--batch", str(b), "--backend", be)
                jcodes[f"{b}|{be}"] = judge("label", "--batch", str(b), "--backend", be, "--notice-acknowledged")
            jcodes[f"{b}|finalize"] = judge("finalize", "--batch", str(b))
            if jcodes[f"{b}|finalize"] != 0:
                raise SystemExit(f"dry run: batch {b} did not finalize: {jcodes}")

    run_stage("A")
    judge_stage("A")
    jcodes["commit-stage-a"] = judge("commit-stage-a")
    run_stage("B")
    judge_stage("B")
    gate = ajd.analysis_gate(work / "runs", dry_run=True)
    # every leg holds one row per record, none failed: an empty leg cannot pass the dry run
    import found10 as f10

    leg_rows: dict[str, int] = {}
    for c, g, r, rep in ar.study_legs():
        rows_path, _ = ar.leg_files(work / "runs", c, g, r, rep)
        rows = [x for x in f10.read_jsonl_repair_tail(rows_path) if x["condition"] == asc.RETRIEVERS[r]]
        leg_rows[asc.leg_id(c, g, r, rep)] = len(rows)
        if len(rows) != len(recs) or any(x.get("failed") for x in rows):
            raise SystemExit(f"dry run: leg {asc.leg_id(c, g, r, rep)} holds {len(rows)} rows for {len(recs)} records")
    # the statistics
    n_g = sum(1 for r in asc.split_records(recs)[0] if r["kind"] == "G")
    scfg = {"records": str(records), "runs_dir": str(work / "runs"), "judge_dir": str(work / "judge"),
            "reach": {"count": n_g, "of": n_g}, "dry_run": True, "n_perm": n_perm, "n_boot": n_boot}
    scfgp = work / "stats-config.json"
    scfgp.write_text(json.dumps(scfg, indent=1))
    rc = ast.main(["--config", str(scfgp), "--out", str(work / "results-dryrun.json"),
                   "--public", str(work / "public-dryrun.json")])
    res = json.loads((work / "results-dryrun.json").read_text())
    return {"legs": codes, "judge": jcodes, "stats_exit": rc, "retrieve_calls": server.calls, "analysis_gate": gate,
            "leg_rows": leg_rows,
            "judge_calls": fake.calls, "layer1": {k: res["layer1"][k]["status"] for k in ("H1", "H2", "H1b")},
            "records": len(recs), "work": str(work)}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--records", required=True, type=Path)
    ap.add_argument("--base-export", required=True, type=Path)
    ap.add_argument("--glossary", required=True, type=Path)
    ap.add_argument("--work", required=True, type=Path)
    ap.add_argument("--study-records", type=Path, default=None)
    ap.add_argument("--added-per-arm", type=int, default=60)
    ap.add_argument("--batch-size", type=int, default=300)
    ap.add_argument("--n-perm", type=int, default=2000)
    ap.add_argument("--n-boot", type=int, default=2000)
    a = ap.parse_args(argv)
    out = dry_run(a.records, a.base_export, a.glossary, a.work, study_records=a.study_records,
                  added_per_arm=a.added_per_arm, batch_size=a.batch_size, n_perm=a.n_perm, n_boot=a.n_boot)
    print(json.dumps(out, indent=1))
    return 0 if out["stats_exit"] == 0 and not any(out["legs"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
