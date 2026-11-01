#!/usr/bin/env python
"""Alias study (OA-1000): the retrieval runner (DRAFT prereg §5, §9). An adapter around
round 3's found@10 runner (``glossbench/found10.py``): every round-3 gate is kept, and the
study adds what its prereg asks on top.

    alias_study_run.py --config CFG plan
    alias_study_run.py --config CFG gold-hashes --base-export CHUNKS.jsonl
    alias_study_run.py --config CFG run --corpus-arm base --glossary none --swap-record SWAP.json \\
                       [--repeat 2] [--chunk-export X --glossary-file F | --rendered-fields R] \\
                       [--boost-declared B] [--probe-max-glossaries N]

**Legs (§5.4).** Four corpus arms (``base``, ``neighbour``, ``random1``, ``random2``) x two
glossary arms (``none``, ``merged``) x three retrievers (``default`` = found10's ``base``
condition: hybrid + rerank; ``bm25``; ``norerank``) = 24 cells, plus one noise repeat per
corpus arm (default x none, ``--repeat 2``) = 28 legs. One ``run`` is one corpus arm with one
glossary arm attached (the swap, ``swap.sh``, happens outside): it measures the three
retrievers together, as round 3 did, so the inactive-reranker gate can compare default with
norerank on the same attachment. A repeat run measures ``default`` only, in the seeded order
``sha256(261101|alias|noise|<corpus arm>|<record>|<condition>)`` (§5.5), so a repeat is not a
replay of the first leg's request sequence. Every record of the accepted file (the 900
balanced and the 191 descriptive) is run in every leg; every call asks ``top_k`` 50, so the
rows carry the top 10 and the depth-50 pool for diagnostics.

**Gates kept from round 3** (found10, unchanged): the swap record's census (chunk total = the
arm's ``expected_chunks``, every stamp the glossary arm's, the glossary file's sha256) and the
optional live census before and after; the depth read-back on every call, with the BM25
exhaustion verifier (``exhausted_verified`` scored, ``clamp`` / ``query_mismatch`` hard
stops, ``under_served`` gated); the glossary-boost probe (``active``, or ``declared`` when not
testable; never ``pass``); hit stamps on bm25 hits; the inactive-reranker gate; > 2 % failed
calls a hard stop; PAUSE / SIGTERM / resume; at most 4 in flight; provenance and ``citable``.

**Added for the study.**

1. *Admin config hash (§9.2).* ``/v1/config`` must be readable (an admin credential) at the
   start, and is read again after the last call; the two canonical-JSON sha256s must be equal,
   else the run is a hard stop. Both hashes are in the summary.
2. *Gold presence (§9.2).* Every record's gold chunk must be in the corpus arm's chunk export
   (``corpora.<arm>.chunk_export``) with the content hash ``gold-hashes`` recorded from the base
   export: an expanded arm holds the base chunks with identical ids and content (§5.1).
3. *Restore point (§9.3).* ``corpora.<arm>.restore_point`` names the JSON the live-ops plan
   wrote before the arm's first store write (``schema alias-restore-point/1``: ``index``,
   ``snapshot_id``, ``total``, ``stamps``): its index must be the arm's, its snapshot id set
   and its total the arm's ``expected_chunks``. Its sha256 is in the summary.
4. *Failed calls per leg (§7, §9.2).* More than 2 % failed calls in any ONE retriever's leg is
   a hard stop (round 3 counted over the run).
5. *The record set (§4.1).* The accepted file's sha256 and the balanced-set id sha must be the
   prereg's; a config with ``dry_run: true`` (``alias_study_dryrun.py``) skips this and must
   name no study record (the dry run refuses them itself).
"""
from __future__ import annotations

import argparse
import asyncio
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
import found10 as f10  # noqa: E402

RESTORE_SCHEMA = "alias-restore-point/1"
STUDY_SCHEMA = "alias-study-run/1"
#: §5.2 / round 3: every retriever asks 50 (the depth-50 pool); default reranks a pool of 50
REQUIRED_TOP_K = 50


def canonical_sha(obj: Any) -> str:
    return asc.sha_text(json.dumps(obj, sort_keys=True, separators=(",", ":")))


def load_config(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return validate_config(raw)


def validate_config(raw: Mapping[str, Any]) -> dict[str, Any]:
    """found10's normalisation plus the study's shape. Raises :class:`found10.Refused`."""
    cfg = f10.normalise_config(dict(raw))
    if set(cfg["corpora"]) != set(asc.CORPUS_ARMS):
        raise f10.Refused(f"corpora must be exactly {list(asc.CORPUS_ARMS)}, got {sorted(cfg['corpora'])}")
    if set(cfg["arms"]) != set(asc.GLOSSARY_ARMS):
        raise f10.Refused(f"arms must be exactly {list(asc.GLOSSARY_ARMS)}, got {sorted(cfg['arms'])}")
    for c, spec in cfg["conditions"].items():
        if int(spec["top_k"]) != REQUIRED_TOP_K:
            raise f10.Refused(f"condition {c}: top_k {spec['top_k']} != {REQUIRED_TOP_K} (the depth-50 pool)")
    if int(cfg["conditions"]["base"].get("rerank_candidates") or 0) != 50:
        raise f10.Refused("condition base (the default retriever): rerank_candidates must be 50 (§5.2)")
    if cfg["conditions"]["base"].get("rerank") is not True or cfg["conditions"]["norerank"].get("rerank") is not False:
        raise f10.Refused("default must rerank and norerank must not (§5.2)")
    sa = cfg["server_assumed"]
    if sa.get("retrieval_candidate_multiplier") != 2 or sa.get("rrf_k") != 60:
        raise f10.Refused("server_assumed must pin retrieval_candidate_multiplier 2 and rrf_k 60 (§5.2)")
    for k in ("records", "out_dir"):
        if not cfg.get(k):
            raise f10.Refused(f"config has no {k!r}")
    if not cfg.get("dry_run"):
        for c in asc.CORPUS_ARMS:
            for k in ("chunk_export", "restore_point"):
                if not cfg["corpora"][c].get(k):
                    raise f10.Refused(f"corpora.{c}.{k} is not set")
        if not cfg.get("gold_hashes"):
            raise f10.Refused("config has no 'gold_hashes' (the base export's gold-chunk content hashes)")
    return cfg


def load_study_records(cfg: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The accepted records in found10's record schema (``corpus`` set per run), with the
    record-set identity check (§4.1) unless ``dry_run``."""
    path = Path(cfg["records"])
    recs = asc.load_records(path)
    if cfg.get("dry_run"):
        ident = {"dry_run": True, "records": len(recs), "sha256": asc.sha256_file(path)}
    else:
        ident = asc.verify_record_set(path, recs)
    return recs, ident


def records_for(recs: Sequence[Mapping[str, Any]], corpus_arm: str) -> list[dict[str, Any]]:
    """found10 filters records by ``corpus``: every record runs in every corpus arm."""
    out = []
    for r in recs:
        out.append({**r, "corpus": corpus_arm, "set": corpus_arm, "stratum": r["kind"]})
    return out


def export_paths(spec: str | Sequence[str]) -> list[Path]:
    """``corpora.<arm>.chunk_export``: one export, or a list (the base export plus the arm's
    added papers) read together."""
    return [Path(spec)] if isinstance(spec, str) else [Path(x) for x in spec]


def check_gold_presence(recs: Sequence[Mapping[str, Any]], exports: Sequence[Path], gold_hashes: Mapping[str, str]
                        ) -> dict[str, Any]:
    """§9.2: every gold chunk in this arm's export(s), with the base export's content hash."""
    want = {r["gold_chunks"][0] for r in recs}
    missing_hash = sorted(c for c in want if c not in gold_hashes)
    if missing_hash:
        raise f10.Refused(f"{len(missing_hash)} gold chunk(s) have no recorded base hash (run gold-hashes)")
    found: dict[str, str] = {}
    chunks = asc.read_chunk_exports(exports)
    for c in want:
        if c in chunks:
            found[c] = asc.sha_text(chunks[c]["content"])
    absent = [c for c in want if c not in found]
    differ = [c for c in want if c in found and found[c] != gold_hashes[c]]
    if absent or differ:
        raise f10.Refused(f"gold presence: {len(absent)} gold chunk(s) absent from the arm's export, "
                          f"{len(differ)} with other content than the base export")
    return {"gold_chunks": len(want), "present": len(found),
            "export_sha256": [asc.sha256_file(x) for x in exports], "chunks_in_export": len(chunks)}


def check_restore_point(path: Path, ccfg: Mapping[str, Any]) -> dict[str, Any]:
    rp = json.loads(path.read_text(encoding="utf-8"))
    problems = []
    if rp.get("schema") != RESTORE_SCHEMA:
        problems.append(f"schema {rp.get('schema')!r} != {RESTORE_SCHEMA!r}")
    if rp.get("index") != ccfg.get("index"):
        problems.append("restore point is for another index")
    if not rp.get("snapshot_id"):
        problems.append("no snapshot_id")
    if rp.get("total") != ccfg.get("expected_chunks"):
        problems.append(f"restore-point total {rp.get('total')} != expected chunks {ccfg.get('expected_chunks')}")
    if problems:
        raise f10.Refused("restore point: " + "; ".join(problems))
    return {"sha256": asc.sha256_file(path), "snapshot_id_sha256": asc.sha_text(str(rp["snapshot_id"]))}


def noise_order(corpus_arm: str) -> Any:
    seed = asc.SEEDS["noise"]

    def key(rec: Mapping[str, Any], cond: str) -> str:
        return asc.seeded_key(seed, corpus_arm, str(rec["record_id"]), cond)
    return key


class AliasRunner(f10.Runner):
    """found10's runner with the study's additions (module docstring, items 1 and 4)."""

    hard_stop: str | None

    def __init__(self, *a: Any, study: Mapping[str, Any] | None = None, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.study = dict(study or {})
        self._config_start: str | None = None

    async def _server_info(self, client: httpx.AsyncClient) -> dict[str, Any]:
        info = await super()._server_info(client)
        if not info.get("config_readable"):
            raise f10.Refused("GET /v1/config is not readable: the admin config-hash check (§9.2) needs an "
                              "admin credential; nothing measured")
        self._config_start = canonical_sha(info["/v1/config"])
        info["config_sha256_start"] = self._config_start
        return info

    async def _after_jobs(self, client: httpx.AsyncClient, server: dict[str, Any]) -> None:
        base = self.cfg["api"]["base_url"].rstrip("/")
        end: str | None = None
        try:
            r = await client.get(base + "/v1/config")
            if r.status_code == 200:
                end = canonical_sha(r.json())
        except (httpx.HTTPError, ValueError):
            end = None
        server["config_sha256_end"] = end
        server["config_hash_identical"] = end is not None and end == self._config_start
        if not server["config_hash_identical"]:
            self.hard_stop = self.hard_stop or (
                "admin /v1/config differs between the start and the end of the leg (or the end read failed): "
                "the leg is re-run after the cause is found and recorded (§9.2)")

    def _finish(self, done: dict[str, dict[str, Any]], swap: Mapping[str, Any], census: Mapping[str, Any],
                live_before: Any, live_after: Any, server: Mapping[str, Any]) -> int:
        per_leg = {}
        expected = len(self.records)
        for cond in self.conditions:
            rows = [r for r in done.values() if r["condition"] == cond]
            failed = sum(bool(r.get("failed")) for r in rows)
            per_leg[cond] = {"rows": len(rows), "failed": failed}
            if len(rows) == expected and rows and failed / len(rows) > f10.MAX_FAILED_SHARE and not self.hard_stop:
                self.hard_stop = (f"{failed}/{len(rows)} calls failed in the {asc.CONDITION_TO_RETRIEVER[cond]} leg "
                                  f"(> {f10.MAX_FAILED_SHARE:.0%}, §7)")
        rc = super()._finish(done, swap, census, live_before, live_after, server)
        summary = json.loads(self.summary_path.read_text(encoding="utf-8"))
        summary["study"] = {"schema": STUDY_SCHEMA, **self.study, "per_leg_failed": per_leg,
                            "retrievers": [asc.CONDITION_TO_RETRIEVER[c] for c in self.conditions],
                            "config_sha256_start": server.get("config_sha256_start"),
                            "config_sha256_end": server.get("config_sha256_end"),
                            "config_hash_identical": server.get("config_hash_identical")}
        f10.write_json_atomic(self.summary_path, summary)
        return rc


def build_runner(cfg: dict[str, Any], recs: Sequence[Mapping[str, Any]], ident: Mapping[str, Any],
                 corpus_arm: str, glossary: str, swap_record: Path, repeat: int | None = None,
                 **kw: Any) -> AliasRunner:
    if corpus_arm not in asc.CORPUS_ARMS:
        raise f10.Refused(f"--corpus-arm {corpus_arm!r}: one of {list(asc.CORPUS_ARMS)}")
    if glossary not in asc.GLOSSARY_ARMS:
        raise f10.Refused(f"--glossary {glossary!r}: one of {list(asc.GLOSSARY_ARMS)}")
    rep = int(repeat or 1)
    if rep not in (1, 2):
        raise f10.Refused("--repeat: 1 or 2 (one noise repeat per corpus arm, §9.4)")
    if rep == 2 and glossary != asc.REPEAT_GLOSSARY:
        raise f10.Refused("the noise repeat is default x none (§5.4)")
    ccfg = cfg["corpora"][corpus_arm]
    extra: dict[str, Any] = {}
    if not cfg.get("dry_run") or ccfg.get("chunk_export"):
        hashes = json.loads(Path(cfg["gold_hashes"]).read_text(encoding="utf-8")) if cfg.get("gold_hashes") else {}
        extra["gold_presence"] = check_gold_presence(recs, export_paths(ccfg["chunk_export"]), hashes)
    if ccfg.get("restore_point"):
        extra["restore_point"] = check_restore_point(Path(ccfg["restore_point"]), ccfg)
    study = {"corpus_arm": corpus_arm, "glossary_arm": glossary, "repeat": rep, "record_set": dict(ident),
             "seeds": {"noise": asc.SEEDS["noise"]} if rep == 2 else {}, "seed_rule": asc.SEED_RULE,
             "dry_run": bool(cfg.get("dry_run")), **extra}
    conds = [asc.RETRIEVERS[asc.REPEAT_RETRIEVER]] if rep == 2 else None
    order = noise_order(corpus_arm) if rep == 2 else None
    return AliasRunner(cfg, records_for(recs, corpus_arm), corpus_arm, glossary, Path(cfg["out_dir"]), swap_record,
                       repeat=rep if rep == 2 else None, conditions=conds, job_order=order, study=study, **kw)


def run_status(out: Path) -> dict[str, Any]:
    """The 28 legs and each one's state from the summaries (``missing`` when never run)."""
    st: dict[str, Any] = {}
    for c, g, r, rep in asc.all_legs():
        label = f10.arm_label(g, rep if rep == 2 else None)
        sp = out / c / f"{label}.summary.json"
        if not sp.is_file():
            st[asc.leg_id(c, g, r, rep)] = "missing"
            continue
        s = json.loads(sp.read_text(encoding="utf-8"))
        cond = s.get("conditions", {}).get(asc.RETRIEVERS[r])
        st[asc.leg_id(c, g, r, rep)] = s["status"] if cond is not None else "missing"
    return st


def cmd_plan(cfg: Mapping[str, Any]) -> int:
    st = run_status(Path(cfg["out_dir"]))
    runs = []
    for c in asc.CORPUS_ARMS:
        for g in asc.GLOSSARY_ARMS:
            runs.append({"run": f"--corpus-arm {c} --glossary {g}", "legs": [st[asc.leg_id(c, g, r)]
                                                                            for r in asc.RETRIEVERS]})
            if g == asc.REPEAT_GLOSSARY:
                runs.append({"run": f"--corpus-arm {c} --glossary {g} --repeat 2",
                             "legs": [st[asc.leg_id(c, g, asc.REPEAT_RETRIEVER, 2)]]})
    print(json.dumps({"legs": len(st), "complete": sum(v == "complete" for v in st.values()), "runs": runs}, indent=1))
    return 0


def cmd_gold_hashes(cfg: Mapping[str, Any], base_export: Path) -> int:
    recs, _ = load_study_records(cfg)
    chunks = asc.read_chunk_exports([base_export])
    want = {r["gold_chunks"][0] for r in recs}
    missing = [c for c in want if c not in chunks]
    if missing:
        print(f"REFUSED: {len(missing)} gold chunk(s) are not in the base export", file=sys.stderr)
        return f10.EXIT_REFUSED
    dst = Path(cfg.get("gold_hashes") or Path(cfg["out_dir"]) / "gold-hashes.json")
    dst.parent.mkdir(parents=True, exist_ok=True)
    f10.write_json_atomic(dst, {c: asc.sha_text(chunks[c]["content"]) for c in sorted(want)})
    print(json.dumps({"gold_chunks": len(want), "out": str(dst), "base_export_sha256": asc.sha256_file(base_export)}))
    return 0


def cmd_run(cfg: dict[str, Any], args: argparse.Namespace,
            client_factory: Any = None, census_client_factory: Any = None) -> int:
    recs, ident = load_study_records(cfg)
    swap = json.loads(Path(args.swap_record).read_text(encoding="utf-8"))
    chunks, unavailable, source = f10.probe_candidates(cfg, args.corpus_arm, args.glossary, swap, args.chunk_export,
                                                       args.glossary_file, args.rendered_fields)
    runner = build_runner(cfg, recs, ident, args.corpus_arm, args.glossary, Path(args.swap_record),
                          repeat=args.repeat, probe_chunks=chunks, probe_unavailable=unavailable,
                          probe_source=source, boost_declared=args.boost_declared,
                          probe_max_glossaries=args.probe_max_glossaries, client_factory=client_factory,
                          census_client_factory=census_client_factory)
    return asyncio.run(runner.run())


def main(argv: Sequence[str] | None = None, *, client_factory: Any = None, census_client_factory: Any = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", required=True, type=Path)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan", help="the 28 legs and their state")
    gh = sub.add_parser("gold-hashes", help="record the gold chunks' content hashes from the base export")
    gh.add_argument("--base-export", required=True, type=Path)
    r = sub.add_parser("run", help="one corpus arm x one glossary arm (three retrievers, or the noise repeat)")
    r.add_argument("--corpus-arm", required=True, choices=asc.CORPUS_ARMS)
    r.add_argument("--glossary", required=True, choices=asc.GLOSSARY_ARMS)
    r.add_argument("--swap-record", required=True)
    r.add_argument("--repeat", type=int, default=None)
    r.add_argument("--chunk-export", default=None)
    r.add_argument("--glossary-file", default=None)
    r.add_argument("--rendered-fields", default=None)
    r.add_argument("--boost-declared", type=float, default=None)
    r.add_argument("--probe-max-glossaries", type=int, default=f10.gbp.PROBE_TOP_K)
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
        if args.cmd == "plan":
            return cmd_plan(cfg)
        if args.cmd == "gold-hashes":
            return cmd_gold_hashes(cfg, args.base_export)
        return cmd_run(cfg, args, client_factory, census_client_factory)
    except f10.Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr, flush=True)
        return f10.EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
