#!/usr/bin/env python
"""Alias study (2026-11): the merged glossaries of the expanded arms' ADDED papers, by the same
pipeline at the same versions as the OA-1000 base merged glossary (prereg §5.3, *For added
papers in every expanded arm*).

Three subcommands, all driven by one PRIVATE config (paths, the endpoint, the pinned image
and the pinned commits; nothing private is in this file):

``plan``
    Verifies the inputs (the arms' chunk files and the draw/selection manifests against the
    corpus counts file, the paper sets against the manifests, no added paper in the base
    corpus), finds each added paper's text shard (the one whose documents the arm's chunks
    were cut from: every chunk's ``content`` equals ``text[start_char:end_char]`` of its
    document as ``JsonlLoader`` reads it), computes the per-document identity
    (``text_sha256`` / ``text_chars`` / ``text_ws_sha256``) from exactly that text, and writes
    the work units: one shard per document long enough to reach the model and one shard of the
    short documents per paper (lines copied byte for byte). The step argv is derived from the
    base run's step summary (every setting; ``--llm-concurrency 1`` per unit, with at most 4
    units in flight, is the one difference and is recorded).

``assemble``
    After the worker (``alias_added_glossaries_worker.py``, run inside the pinned tools image)
    has written every unit: checks every row's identity against the plan, merges the unit
    files with the image's ``merge_files``, re-derives ``--merge llm-only`` and then
    ``--attach mention`` with ``scripts/glossary_file.py`` at the commits that re-derived the
    base arm file, writes the added papers' chunk export, runs ``alias_abbrev.py`` at the
    commit that built the base merged glossary (abbreviation entries, and the merge on the
    (paper, alias, primary words) key with the ``kind`` rule), and writes the manifest.

``status``
    Counts finished units from the ledger and the unit files (no writes).

No model call is made here (the worker makes them), and no store, workflow or gateway is
touched. Outputs are private: a directory outside the repository, 0700, files 0600.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
REPO = _HERE.parents[2]
ARMS = ("neighbour", "random-1", "random-2")
TEXT_SUFFIXES = ("", "-v2", "-v3", "-v4")
#: the LLM settings the base step summary must state, mapped to the step script's flags
_FLAG = {
    "timeout_s": "--llm-timeout-s", "max_tokens": "--llm-max-tokens", "window_chars": "--llm-window-chars",
    "max_windows": "--llm-max-windows", "repair_rounds": "--llm-repair-rounds",
    "cutoff_retry": "--llm-cutoff-retry", "min_chars": "--llm-min-chars", "seed": "--llm-seed",
    "api_key_env": "--llm-api-key-env", "model": "--llm-model",
}


# ---------------------------------------------------------------- small helpers
def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_file(p: str | Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def write_private(path: Path, data: str | bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data.encode("utf-8") if isinstance(data, str) else data)
    os.replace(tmp, path)


def write_json(path: Path, obj: Any) -> None:
    write_private(path, json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n")


def private_dir(p: Path) -> Path:
    if p.resolve() == REPO or REPO in p.resolve().parents:
        raise SystemExit(f"refusing to write private outputs inside the repository: {p}")
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(p, 0o700)
    return p


def load_config(path: str) -> dict[str, Any]:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    need = ("corpus_root", "out_dir", "image", "image_sha256", "step_script", "endpoint", "model",
            "base_step_summary", "base_arm", "base_arm_sha256", "base_export", "base_export_sha256",
            "corpus_commit", "workflow_id", "tool_image", "abbrev_commit", "rederive_merge_commit",
            "rederive_attach_commit", "worktrees", "python")
    missing = [k for k in need if k not in cfg]
    if missing:
        raise SystemExit(f"config: missing {missing}")
    return cfg


def provenance() -> dict[str, Any]:
    from ragstack.provenance import experiment_provenance

    return experiment_provenance(repo=str(REPO))


def chunk_rows(path: Path) -> Iterable[dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def paper_of(row: Mapping[str, Any]) -> str:
    sp = (row.get("metadata") or {}).get("source_path") or ""
    return sp.split("#", 1)[0] if sp else row["doc_id"]


# ---------------------------------------------------------------- plan
def verify_corpus(cfg: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, list[dict[str, Any]]]]]:
    """Counts-file checks, arm paper sets vs the manifests, chunks grouped by arm and paper."""
    root = Path(cfg["corpus_root"])
    man = root / "manifests"
    counts_p = man / "counts-v4.json"
    counts = json.loads(counts_p.read_text())
    rec: dict[str, Any] = {"counts_file_sha256": sha256_file(counts_p), "arms": {}}
    bad = [k for k, v in counts["stage_provenance_commits"].items() if v != cfg["corpus_commit"]]
    if bad:
        raise SystemExit(f"corpus stages not at the pinned commit: {bad}")
    for name, want in counts["manifest_sha256"].items():
        got = sha256_file(man / name)
        if got != want:
            raise SystemExit(f"manifest {name}: sha256 {got} != counts file {want}")
    rec["manifest_sha256"] = dict(counts["manifest_sha256"])
    by_arm: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for arm in ARMS:
        p = root / f"chunks-{arm}-v4.jsonl"
        sha = sha256_file(p)
        if sha != counts[arm]["chunks_sha256"]:
            raise SystemExit(f"{p.name}: sha256 != counts file")
        papers: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in chunk_rows(p):
            papers[paper_of(r)].append(r)
        if len(papers) != counts[arm]["papers"]:
            raise SystemExit(f"{arm}: {len(papers)} papers != counts file {counts[arm]['papers']}")
        by_arm[arm] = dict(papers)
        rec["arms"][arm] = {"chunks_file_sha256": sha, "papers": len(papers),
                            "chunks": sum(len(v) for v in papers.values())}
    # paper sets against the manifests: each listed work maps to exactly one chunk-file paper
    # (its PMC id when converted from JATS, else its work id), and together they are the set
    sel = json.loads((man / "neighbour-selection-v4.json").read_text())
    lists = {"neighbour": (sel["works"], [w for v in sel["attributed"].values() for w, _ in v])}
    for arm in ("random-1", "random-2"):
        d = json.loads((man / f"{arm}-draw-v4.json").read_text())
        lists[arm] = (d["works"], [t["wid"] for v in d["per_study"].values() for t in v["took"]])
    for arm, (works, wids) in lists.items():
        have = set(by_arm[arm])
        keys = []
        for w in wids:
            cand = [k for k in dict.fromkeys((w, (works.get(w) or {}).get("pmcid"))) if k and k in have]
            if len(cand) != 1:
                raise SystemExit(f"{arm}: a listed work maps to {len(cand)} chunk-file papers")
            keys.append(cand[0])
        if len(set(keys)) != len(keys) or set(keys) != have:
            raise SystemExit(f"{arm}: the manifest's papers are not the chunk file's papers")
        rec["arms"][arm]["manifest_works"] = len(wids)
        rec["arms"][arm]["manifest_matches_chunks"] = True
    r1, r2, nb = set(by_arm["random-1"]), set(by_arm["random-2"]), set(by_arm["neighbour"])
    rec["overlap"] = {"random-1&random-2": len(r1 & r2), "neighbour&random-1": len(nb & r1),
                      "neighbour&random-2": len(nb & r2)}
    if len(r1 & r2) != counts["draw_overlap"]:
        raise SystemExit("random draw overlap != counts file")
    # a paper shared by two arms has the same chunks in both
    for p in r1 & r2:
        if by_arm["random-1"][p] != by_arm["random-2"][p]:
            raise SystemExit("a paper shared by the random draws has different chunks in the two files")
    for a, b in (("neighbour", "random-1"), ("neighbour", "random-2")):
        for p in set(by_arm[a]) & set(by_arm[b]):
            if by_arm[a][p] != by_arm[b][p]:
                raise SystemExit(f"a paper shared by {a} and {b} has different chunks")
    # no added paper is a base (OA-1000) paper
    if sha256_file(cfg["base_export"]) != cfg["base_export_sha256"]:
        raise SystemExit("base export sha256 mismatch")
    base = {paper_of(r) for r in chunk_rows(Path(cfg["base_export"]))}
    added = nb | r1 | r2
    if added & base:
        raise SystemExit(f"{len(added & base)} added paper(s) are base papers")
    rec["base_papers"] = len(base)
    rec["added_papers_distinct"] = len(added)
    return rec, by_arm


def locate_and_identify(cfg: Mapping[str, Any], papers: Mapping[str, list[dict[str, Any]]],
                        min_chars: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Per paper: the text shard the chunks were cut from, its raw lines paired with the
    loader's documents, and every document's identity from exactly that text."""
    from ragstack.ingestion.glossary_file import masked_key
    from ragstack.ingestion.loaders import JsonlLoader
    from ragstack.ingestion.symbol_glossary import content_key

    root = Path(cfg["corpus_root"])
    loader = JsonlLoader()
    out: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    for key in sorted(papers):
        chunks = papers[key]
        matched = []
        for sfx in TEXT_SUFFIXES:
            p = root / f"text{sfx}" / f"{key}.jsonl"
            if not p.exists():
                continue
            docs = loader.load(str(p))
            text = {d.id: d.content for d in docs}
            ok = len(text) == len(docs) and all(
                c["doc_id"] in text and text[c["doc_id"]][c["start_char"]:c["end_char"]] == c["content"]
                for c in chunks)
            if ok:
                matched.append((sfx, p, docs))
        if not matched:
            raise SystemExit("a paper has no text shard its chunks were cut from")
        shas = {sha256_file(p) for _, p, _ in matched}
        if len(shas) != 1:
            raise SystemExit("a paper has two different text shards that both match its chunks")
        stats[f"shard_dir:text{matched[-1][0]}"] += 1
        if len(matched) > 1:
            stats["papers_with_identical_shard_in_two_dirs"] += 1
        sfx, path, docs = matched[-1]
        raw = [ln for ln in path.read_bytes().splitlines(keepends=True) if ln.strip()]
        if len(raw) != len(docs):
            raise SystemExit("a shard's non-blank lines do not pair one-to-one with its documents")
        with_chunks = {c["doc_id"] for c in chunks}
        recs = []
        for i, (ln, d) in enumerate(zip(raw, docs, strict=True)):
            if not ln.endswith(b"\n"):
                ln += b"\n"
            recs.append({
                "i": i, "doc_id": d.id, "line": ln, "long": len(d.content) >= min_chars,
                "text_sha256": content_key(d.content), "text_chars": len(d.content),
                "text_ws_sha256": masked_key(d.content), "has_chunks": d.id in with_chunks,
                "n_chunks": sum(c["doc_id"] == d.id for c in chunks),
            })
        stats["documents"] += len(recs)
        stats["documents_long"] += sum(r["long"] for r in recs)
        stats["documents_without_chunks"] += sum(not r["has_chunks"] for r in recs)
        out.append({"paper": key, "shard": str(path), "shard_sha256": next(iter(shas)), "docs": recs})
    return out, dict(stats)


def step_argv(cfg: Mapping[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """The step flags, every value read from the base run's step summary."""
    summ = json.loads(Path(cfg["base_step_summary"]).read_text())
    s = summ["settings"]
    if not s.get("llm"):
        raise SystemExit("the base step summary is not an LLM run")
    if s["model"] != cfg["model"]:
        raise SystemExit("config model != the base run's model")
    if s.get("extra_body") is not None or s.get("deadline_s"):
        raise SystemExit("the base run used extra_body or a deadline: extend step_argv first")
    want_endpoint = cfg["endpoint"].split("://", 1)[-1].rstrip("/")
    if want_endpoint != s["endpoint_name"]:
        raise SystemExit("config endpoint != the base run's endpoint")
    thinking = {False: "off", True: "on", None: "unset"}[s["thinking"]]
    argv = ["--llm", "on", "--llm-endpoint", cfg["endpoint"], "--llm-thinking", thinking,
            "--llm-concurrency", "1"]
    for k, flag in _FLAG.items():
        argv += [flag, str(s[k])]
    argv += ["--workflow-id", cfg["workflow_id"], "--tool-image", cfg["tool_image"],
             "--tool-image-digest", cfg["image_sha256"]]
    rec = {"base_settings": s, "base_provenance": summ.get("provenance"), "base_probe": summ.get("probe"),
           "differences": {"concurrency": {"base": s["concurrency"],
                                           "here": "1 per unit, at most 4 units in flight",
                                           "why": "the endpoint admits 4 sequences; calls within a "
                                                  "document are sequential in the step"},
                           "workflow_id": {"base": (summ.get("provenance") or {}).get("workflow_id"),
                                           "here": cfg["workflow_id"]}}}
    return argv, rec


def cmd_plan(cfg: Mapping[str, Any]) -> int:
    out = private_dir(Path(cfg["out_dir"]))
    work = private_dir(out / "work")
    units_dir = private_dir(work / "units")
    rows_dir = private_dir(work / "rows")
    if sha256_file(cfg["image"]) != cfg["image_sha256"]:
        raise SystemExit("tools image sha256 != the pinned digest")
    if sha256_file(cfg["base_arm"]) != cfg["base_arm_sha256"]:
        raise SystemExit("base arm file sha256 mismatch")
    argv, argv_rec = step_argv(cfg)
    min_chars = int(argv[argv.index("--llm-min-chars") + 1])
    corpus_rec, by_arm = verify_corpus(cfg)
    papers: dict[str, list[dict[str, Any]]] = {}
    arm_of: dict[str, list[str]] = defaultdict(list)
    for arm in ARMS:
        for p, rows in by_arm[arm].items():
            papers.setdefault(p, rows)
            arm_of[p].append(arm)
    located, stats = locate_and_identify(cfg, papers, min_chars)
    units: list[dict[str, Any]] = []
    identity: list[dict[str, Any]] = []
    for pi, rec in enumerate(located):
        short = [d for d in rec["docs"] if not d["long"]]
        groups = [(f"p{pi:05d}-s", short)] if short else []
        groups += [(f"p{pi:05d}-d{d['i']:03d}", [d]) for d in rec["docs"] if d["long"]]
        for uid, docs in groups:
            shard = units_dir / f"{uid}.jsonl"
            data = b"".join(d["line"] for d in docs)
            if not shard.exists() or shard.read_bytes() != data:
                write_private(shard, data)
            units.append({"unit": uid, "paper_index": pi, "shard": str(shard),
                          "out": str(rows_dir / f"{uid}.glossary.jsonl"),
                          "summary": str(rows_dir / f"{uid}.summary.json"),
                          "doc_ids": [d["doc_id"] for d in docs], "long": docs[0]["long"]})
        for d in rec["docs"]:
            identity.append({"paper": rec["paper"], "paper_index": pi, "arms": arm_of[rec["paper"]],
                             "doc_id": d["doc_id"], "text_sha256": d["text_sha256"],
                             "text_chars": d["text_chars"], "text_ws_sha256": d["text_ws_sha256"],
                             "long": d["long"], "n_chunks": d["n_chunks"],
                             "shard_sha256": rec["shard_sha256"]})
    write_private(work / "identity.jsonl",
                  "".join(json.dumps(r, sort_keys=True) + "\n" for r in identity))
    plan = {
        "created": now(), "provenance": provenance(), "work_dir": str(work),
        "step_script": cfg["step_script"], "model": cfg["model"], "step_argv": argv,
        "step_argv_record": argv_rec, "corpus": corpus_rec, "located": stats,
        "papers": len(located), "documents": len(identity),
        "units": units, "unit_counts": {"total": len(units), "long": sum(u["long"] for u in units),
                                        "short_groups": sum(not u["long"] for u in units)},
        "identity_sha256": sha256_file(work / "identity.jsonl"),
        "papers_by_arm": {arm: sorted(by_arm[arm]) for arm in ARMS},
        "paper_index": [r["paper"] for r in located],
    }
    write_json(work / "plan.json", plan)
    print(json.dumps({"papers": plan["papers"], "documents": plan["documents"], "units": plan["unit_counts"],
                      "located": stats, "overlap": corpus_rec["overlap"],
                      "citable": plan["provenance"].get("dirty") is False}, indent=1))
    return 0


# ---------------------------------------------------------------- status
def cmd_status(cfg: Mapping[str, Any]) -> int:
    work = Path(cfg["out_dir"]) / "work"
    plan = json.loads((work / "plan.json").read_text())
    have = sum(Path(u["out"]).exists() for u in plan["units"])
    deg: Counter[str] = Counter()
    calls = 0
    led = work / "ledger.jsonl"
    if led.exists():
        last: dict[str, dict[str, Any]] = {}
        for line in open(led):
            r = json.loads(line)
            last[r["unit"]] = r
        for r in last.values():
            for k, v in (r.get("degraded_reasons") or {}).items():
                deg[k] += v
            calls += r.get("llm_calls") or 0
    print(json.dumps({"units": len(plan["units"]), "with_output": have, "degraded": dict(deg),
                      "llm_calls": calls, "stop": (work / "STOP").exists()}, indent=1))
    return 0


# ---------------------------------------------------------------- assemble
def _run(cmd: list[str], env: Mapping[str, str] | None = None, cwd: str | None = None) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, env=dict(env) if env else None, cwd=cwd)
    if p.returncode != 0:
        raise SystemExit(f"command failed ({p.returncode}): {' '.join(cmd[:4])} ...\n{p.stderr[-3000:]}")
    return p.stdout


def _host_env(worktree: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "VIRTUAL_ENV"))}
    env["PYTHONPATH"] = str(Path(worktree) / "python")
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _git_head(worktree: str) -> tuple[str, bool]:
    head = _run(["git", "-C", worktree, "rev-parse", "HEAD"]).strip()
    dirty = bool(_run(["git", "-C", worktree, "status", "--porcelain"]).strip())
    return head, dirty


def launch_records(work: Path) -> dict[str, Any]:
    """Every ``<name>.launch.json`` beside a pid file: the pid, the argv, the worker's sha256 and
    the commit it was launched from (F7). Records written before the launcher wrote one JSON
    object (a JSON object, then a ``sha256sum`` line, then a commit line) are read too and
    marked ``legacy``. The worker's sha256 must equal the file at that commit."""
    recs: dict[str, Any] = {}
    for p in sorted(work.glob("*.launch.json")):
        text = p.read_text(encoding="utf-8")
        try:
            obj = json.loads(text)
            fmt = "json"
        except json.JSONDecodeError:
            try:
                obj, end = json.JSONDecoder().raw_decode(text)
                rest = text[end:].split()
                obj = {**obj, "worker_sha256": rest[0], "worker_path": rest[1], "launch_commit": rest[2]}
            except (json.JSONDecodeError, IndexError):
                raise SystemExit(f"{p.name}: neither a JSON launch record nor a legacy one") from None
            fmt = "legacy"
        missing = [k for k in ("pid", "argv", "worker_sha256", "launch_commit") if k not in obj]
        if missing:
            raise SystemExit(f"{p.name}: launch record lacks {missing}")
        if obj.get("worker_dirty"):
            raise SystemExit(f"{p.name}: the worker had uncommitted changes at launch")
        commit = obj["launch_commit"]
        got = subprocess.run(["git", "-C", str(REPO), "show",
                              f"{commit}:python/scripts/eval/alias_added_glossaries_worker.py"],
                             capture_output=True)
        if got.returncode != 0:
            raise SystemExit(f"{p.name}: the worker is not readable at launch commit {commit!r} "
                             f"({got.stderr.decode(errors='replace').strip()[:200]})")
        blob = got.stdout
        if hashlib.sha256(blob).hexdigest() != obj["worker_sha256"]:
            raise SystemExit(f"{p.name}: the worker's sha256 is not the file at the launch commit")
        recs[p.name.removesuffix(".launch.json")] = {
            "pid": obj["pid"], "argv": obj["argv"], "worker_sha256": obj["worker_sha256"],
            "launch_commit": commit, "worker_matches_commit": True, "record_format": fmt,
            "record_sha256": sha256_file(p)}
    if not recs:
        raise SystemExit("no launch record beside the ledger")
    return recs


def _generate(cfg: Mapping[str, Any], plan: Mapping[str, Any], out: Path, work: Path, step_p: Path,
              llmonly_p: Path, mention_p: Path, export_p: Path, abbrev_dir: Path,
              review_dir: Path) -> dict[str, str]:
    """Steps 2-5 of ``assemble``: merge, the rederive chain, the export, ``alias_abbrev``.
    Returns the commits that ran."""
    # 2. merge the unit files (the image's merge_files: refuses a doc id in two inputs and
    #    mixed configs; rows in input order; producer merge(<inputs' producers>))
    list_p = work / "unit-files.txt"
    write_private(list_p, "".join(u["out"] + "\n" for u in plan["units"]))
    binds = sorted({str(out), str(Path(cfg["out_dir"]).resolve())})
    code = ("import sys; from ragstack.ingestion.glossary_file import merge_files; "
            "paths=[l.strip() for l in open(sys.argv[1]) if l.strip()]; "
            "print(merge_files(paths, sys.argv[2]))")
    _run(["apptainer", "exec", "--cleanenv", "--no-home", *sum((["--bind", b] for b in binds), []),
          cfg["image"], "python", "-c", code, str(list_p), str(step_p)])
    os.chmod(step_p, 0o600)
    # 3. the base arm chain: rederive --merge llm-only, then --attach mention, each with the
    #    glossary_file.py of the commit that re-derived the base arm file
    wt = cfg["worktrees"]
    commits: dict[str, str] = {}
    for key, args_, src, dst in (("rederive_merge_commit", ["--merge", "llm-only"], step_p, llmonly_p),
                                 ("rederive_attach_commit", ["--attach", "mention"], llmonly_p, mention_p)):
        tree = wt[key]
        head, dirty = _git_head(tree)
        if not head.startswith(cfg[key]) or dirty:
            raise SystemExit(f"{key}: worktree not clean at {cfg[key]}")
        commits[key] = head
        _run([cfg["python"], str(Path(tree) / "python/scripts/glossary_file.py"), "rederive", *args_,
              str(src), "--out", str(dst)], env=_host_env(tree))
        os.chmod(dst, 0o600)
    # 4. the added papers' chunk export (arm order, a shared paper once; same rows in every arm)
    seen_c: set[str] = set()
    lines: list[str] = []
    root = Path(cfg["corpus_root"])
    for arm in ARMS:
        for r in chunk_rows(root / f"chunks-{arm}-v4.jsonl"):
            if r["chunk_id"] in seen_c:
                continue
            seen_c.add(r["chunk_id"])
            lines.append(json.dumps(r, ensure_ascii=False) + "\n")
    write_private(export_p, "".join(lines))
    # 5. alias_abbrev.py at the commit that built the base merged glossary
    tree = wt["abbrev_commit"]
    head, dirty = _git_head(tree)
    if not head.startswith(cfg["abbrev_commit"]) or dirty:
        raise SystemExit("alias_abbrev worktree not clean at the pinned commit")
    commits["abbrev_commit"] = head
    private_dir(abbrev_dir)
    private_dir(review_dir)
    _run([cfg["python"], str(Path(tree) / "python/scripts/eval/alias_abbrev.py"), "--export", str(export_p),
          "--arm", str(mention_p), "--out-dir", str(abbrev_dir), "--review-dir", str(review_dir),
          "--paper-key", "path_root"], env=_host_env(tree), cwd=str(abbrev_dir))
    for p in (*abbrev_dir.iterdir(), *review_dir.iterdir()):
        os.chmod(p, 0o600)
    return commits


def cmd_assemble(cfg: Mapping[str, Any], allow_degraded_transport: bool, manifest_only: bool = False) -> int:
    out = Path(cfg["out_dir"])
    work = out / "work"
    plan = json.loads((work / "plan.json").read_text())
    if (work / "STOP").exists():
        raise SystemExit("STOP present: the served model id did not match; nothing is assembled")
    from ragstack.ingestion.glossary_file import read_glossary_file

    identity = {r["doc_id"]: r for r in chunk_rows(work / "identity.jsonl")}
    # 1. every unit written, every row's identity equals the plan's (computed from the very
    #    text the chunks were cut from), degraded documents counted by reason
    deg: Counter[str] = Counter()
    deg_docs: list[dict[str, Any]] = []
    served: Counter[str] = Counter()
    calls = 0
    unit_calls: dict[str, int] = {}
    seen: set[str] = set()
    for u in plan["units"]:
        unit_calls[u["unit"]] = 0
        gf = read_glossary_file(u["out"])
        if sorted(gf.rows) != sorted(u["doc_ids"]):
            raise SystemExit(f"unit {u['unit']}: rows != planned documents")
        for did, row in gf.rows.items():
            want = identity[did]
            got = (row.text_sha256, row.text_chars, row.text_ws_sha256)
            if got != (want["text_sha256"], want["text_chars"], want["text_ws_sha256"]):
                raise SystemExit(f"unit {u['unit']}: a row's identity differs from the plan's")
            if did in seen:
                raise SystemExit("a document has two rows")
            seen.add(did)
            lp = (row.provenance or {}).get("llm") or {}
            if lp.get("served"):
                served[json.dumps(lp["served"], sort_keys=True)] += 1
            calls += len(lp.get("calls") or [])
            unit_calls[u["unit"]] += len(lp.get("calls") or [])
            if row.degraded:
                deg[row.degraded] += 1
                deg_docs.append({"doc_id": did, "unit": u["unit"], "reason": row.degraded})
    transport = {k: v for k, v in deg.items() if k != "cut_off"}
    if transport and not allow_degraded_transport:
        units_ = sorted({d["unit"] for d in deg_docs if d["reason"] != "cut_off"})
        raise SystemExit(f"degraded by the transport, not the model: {transport} in units {units_}; "
                         "re-run them first: list the unit ids in a file and run the worker with "
                         "--only FILE --rerun (their verified outputs are moved aside, not deleted), "
                         "or pass --allow-degraded-transport to record them as they are")
    if seen != set(identity):
        raise SystemExit("documents without a row")
    # 1b. the ledger (F1): every unit has an ok line whose out_sha256 is the file on disk (the
    #     last ok line counts: a re-run supersedes), and the replies the worker checked add up
    #     to the calls the rows record — so no call escaped the served-model check
    last: dict[str, dict[str, Any]] = {}
    if (work / "ledger.jsonl").exists():
        for x in open(work / "ledger.jsonl"):
            r = json.loads(x)
            if r.get("ok"):
                last[r["unit"]] = r
    missing = [u["unit"] for u in plan["units"] if u["unit"] not in last]
    if missing:
        raise SystemExit(f"{len(missing)} unit(s) have no ok ledger line, e.g. {missing[:5]}")
    stale = [u["unit"] for u in plan["units"] if last[u["unit"]].get("out_sha256") != sha256_file(u["out"])]
    if stale:
        raise SystemExit(f"{len(stale)} unit file(s) differ from their ledger sha256, e.g. {stale[:5]}")
    served_replies: Counter[str] = Counter()
    for u in plan["units"]:
        served_replies.update(last[u["unit"]].get("served_replies") or {})
    mismatched = {k: v for k, v in served_replies.items() if k != cfg["model"]}
    if mismatched:
        raise SystemExit(f"replies from another model in the ledger: {mismatched}")
    off = [u["unit"] for u in plan["units"]
           if sum((last[u["unit"]].get("served_replies") or {}).values()) != unit_calls[u["unit"]]]
    if off:
        raise SystemExit(f"{len(off)} unit(s) whose checked replies != the calls their rows record, "
                         f"e.g. {off[:5]}")
    if sum(served_replies.values()) != calls:
        raise SystemExit(f"checked replies {sum(served_replies.values())} != calls in the rows {calls}")
    workers = launch_records(work)
    step_p = out / "added-v4.qwen36.step.jsonl"
    llmonly_p = out / "added-v4.qwen36p.jsonl"
    mention_p = out / "added-v4.qwen36p.mention.jsonl"
    export_p = work / "chunks-added-v4.jsonl"
    abbrev_dir = out / "abbrev"
    review_dir = out / "review"
    commits: dict[str, str] = {}
    prev: dict[str, Any] | None = None
    if manifest_only:
        # F7/F2 additions only: the outputs are not regenerated; they must be byte for byte the
        # ones the previous manifest recorded, whose pinned commits are carried over
        if not (out / "manifest.json").is_file():
            raise SystemExit("--manifest-only: no previous manifest.json to rebuild from")
        prev = json.loads((out / "manifest.json").read_text())
        commits = {**prev["chain"]["rederive_commits"],
                   "abbrev_commit": prev["abbreviation"]["alias_abbrev_commit"]}
    else:
        commits = _generate(cfg, plan, out, work, step_p, llmonly_p, mention_p, export_p,
                            abbrev_dir, review_dir)
    merged_p = abbrev_dir / "merged_glossary.jsonl"
    recount = json.loads((abbrev_dir / "recount.json").read_text())
    # 6. census by kind per added paper (every added paper listed, zeros included)
    census: dict[str, Counter[str]] = {p: Counter() for p in plan["paper_index"]}
    for e in chunk_rows(merged_p):
        census.setdefault(e["paper"], Counter())[e["kind"]] += 1
    extra = sorted(set(census) - set(plan["paper_index"]))
    if extra:
        raise SystemExit(f"{len(extra)} merged-glossary paper(s) are not added papers")
    kinds = ("symbol", "abbreviation", "both")
    totals = Counter()
    for c in census.values():
        totals.update(c)
    by_arm = {}
    for arm, ps in plan["papers_by_arm"].items():
        t: Counter[str] = Counter()
        for p in ps:
            t.update(census[p])
        by_arm[arm] = {"papers": len(ps), "papers_with_entry": sum(bool(census[p]) for p in ps),
                       **{k: t[k] for k in kinds}, "entries": sum(t.values())}
    mention_head = json.loads(open(mention_p, encoding="utf-8").readline())
    base_head = json.loads(open(cfg["base_arm"], encoding="utf-8").readline())
    outputs = {p.name: sha256_file(p) for p in (step_p, llmonly_p, mention_p)}
    outputs.update({f"abbrev/{p.name}": sha256_file(p) for p in sorted(abbrev_dir.iterdir())})
    outputs.update({f"review/{p.name}": sha256_file(p) for p in sorted(review_dir.iterdir())})
    outputs["work/chunks-added-v4.jsonl"] = sha256_file(export_p)
    outputs["work/identity.jsonl"] = sha256_file(work / "identity.jsonl")
    outputs["work/plan.json"] = sha256_file(work / "plan.json")
    if prev is not None:
        if outputs != prev["outputs_sha256"]:
            changed = sorted(k for k in set(outputs) | set(prev["outputs_sha256"])
                             if outputs.get(k) != prev["outputs_sha256"].get(k))
            raise SystemExit(f"--manifest-only: outputs differ from the previous manifest: {changed}")
        old = out / "manifest.json"
        keep = out / f"manifest.superseded-{sha256_file(old)[:8]}.json"
        if not keep.exists():
            write_private(keep, old.read_bytes())
    versions = set()
    with open(mention_p, encoding="utf-8") as fh:
        fh.readline()
        for x in fh:
            lp = (json.loads(x).get("provenance") or {}).get("llm")
            if lp:
                versions.add(lp["version"])
    if len(versions) != 1:
        raise SystemExit(f"LLM versions in the arm file: {sorted(versions)}")
    llm_version = versions.pop()
    manifest = {
        "schema": "alias-study.added-glossaries/1", "created": now(), "provenance": provenance(),
        "prereg": "§5.3 For added papers in every expanded arm",
        "corpus": plan["corpus"], "papers": plan["papers"], "documents": plan["documents"],
        "papers_by_arm": {a: len(v) for a, v in plan["papers_by_arm"].items()},
        "symbol_step": {
            "llm_version": llm_version, "prompt_hash": llm_version.split(":")[2],
            "rules_version": "rules/3", "requested_model": cfg["model"],
            "served_model_ids_per_reply": dict(served_replies),
            "served_probe_records": dict(served),
            "step_argv": plan["step_argv"], "argv_record": plan["step_argv_record"],
            "tool_image": cfg["tool_image"], "tool_image_sha256": cfg["image_sha256"],
            "step_script": cfg["step_script"], "llm_calls": calls,
            "units": plan["unit_counts"],
        },
        "chain": {"step_file_header": json.loads(open(step_p, encoding="utf-8").readline()),
                  "arm_file_header": mention_head, "base_arm_file_header": base_head,
                  "rederive_commits": {k: commits[k] for k in ("rederive_merge_commit", "rederive_attach_commit")}},
        "abbreviation": {"alias_abbrev_commit": commits["abbrev_commit"], "recount_provenance": recount["provenance"],
                         "abbreviation_build": recount["abbreviation_build"], "corpus": recount["corpus"],
                         "inputs": recount["inputs"]},
        "merge_rule": "alias_abbrev.merge at the pinned commit: key (paper, alias, primary words); "
                      "symbol + abbreviation on one key -> kind both",
        "degraded": {"documents": sum(deg.values()), "by_reason": dict(deg), "docs": deg_docs},
        "census": {"totals": {k: totals[k] for k in kinds}, "entries": sum(totals.values()),
                   "papers_with_entry": sum(bool(c) for c in census.values()),
                   "by_arm": by_arm,
                   "per_paper": {p: {k: c[k] for k in kinds} for p, c in sorted(census.items())}},
        "identity": {"rule": "text_sha256/text_chars/text_ws_sha256 of every row equal the values computed "
                             "from the JsonlLoader text every chunk of the document was cut from "
                             "(content == text[start_char:end_char] for every chunk)",
                     "documents_checked": len(seen), "mismatches": 0,
                     "documents_without_chunks": plan["located"].get("documents_without_chunks", 0)},
        "outputs_sha256": outputs,
        "worker": workers,
        "per_arm_glossary_files": (
            "Not written here, by design. Prereg 5.3's per-arm glossary files are the per-collection "
            "ragstack.glossary_file/v1 files the glossary-file writer builds for each (corpus arm, "
            "glossary arm) load, covering base and that arm's added documents together; the writer "
            "records each file's sha256 in its own manifest. They are built from this directory's "
            "per-document rows and merged glossary, so no separate per-arm slices of these outputs "
            "are needed. Per-arm counts are in census.by_arm."),
        "supersedes": (None if prev is None else {"manifest_sha256": sha256_file(out / "manifest.json"),
                                                   "note": "manifest additions only; outputs byte-identical"}),
    }
    write_json(out / "manifest.json", manifest)
    print(json.dumps({"outputs": outputs, "census": manifest["census"]["totals"], "degraded": dict(deg)}, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("plan", "status", "assemble"))
    ap.add_argument("--config", required=True, help="the private run config (JSON)")
    ap.add_argument("--allow-degraded-transport", action="store_true",
                    help="assemble even with documents degraded by the transport (recorded)")
    ap.add_argument("--manifest-only", action="store_true",
                    help="assemble: rebuild the manifest from the existing outputs (all checks re-run; "
                         "refused unless every output is byte for byte the previous manifest's)")
    ap.add_argument("--allow-dirty", action="store_true", help="development only: not citable")
    a = ap.parse_args(argv)
    os.umask(0o077)
    if a.manifest_only and a.command != "assemble":
        raise SystemExit("--manifest-only applies to assemble only")
    if a.allow_degraded_transport and a.command != "assemble":
        raise SystemExit("--allow-degraded-transport applies to assemble only")
    cfg = load_config(a.config)
    if a.command != "status":
        prov = provenance()
        if not (prov.get("commit") and prov.get("dirty") is False and prov.get("untracked") == 0) \
                and not a.allow_dirty:
            raise SystemExit("tree is dirty/untracked or commit unknown; commit first (or --allow-dirty)")
    if a.command == "plan":
        return cmd_plan(cfg)
    if a.command == "status":
        return cmd_status(cfg)
    return cmd_assemble(cfg, a.allow_degraded_transport, a.manifest_only)


if __name__ == "__main__":
    raise SystemExit(main())
