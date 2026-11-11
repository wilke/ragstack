"""``h2h_report.py configs``: the decision (three-arm) and reporting (five-arm) harness
configs per setting, derived key by key from the frozen Addendum C configs (PREREG §4.1
"Implementation, two configs", §9.1).

    h2h_report.py configs --addendum-c a=<config.json> d=<config.json> b=... c=...
        --out-root <dir> [--collection-s oa-eval-s --collection-sregex oa-eval-sregex]
        [--public-diff <path>]

For each setting it writes ``<out-root>/<setting>/decision.config.json`` and
``reporting.config.json``. Everything is copied from the Addendum C config except:

* ``arms`` — decision: ft512o64 (baseline, ``cost_rank`` 1), S (2), ft512o64s (3), the
  owner's Order B ranks (§4.2, §10 Q1); reporting: those three plus S-regex (4) and sent512s
  (5) — the reporting config is scored only, its ranks are never used;
* ``out_dir`` — ``<out-root>/out-h2h-dec-<setting>`` / ``out-h2h-rep-<setting>``;
* ``decide`` — ``tie_break: paired_ladder``, ``stratum_correction: bonferroni``, ``k`` 5,
  ``min_stratum_n`` 30 (asserted equal to Addendum C's);
* ``_comment`` / ``_derived_from`` (ignored by the loader; the source config's sha256).

``margins``, ``score.exclude_noise_subset``, ``api`` (request fields, ``top_k``), ``noise``,
``sample``, ``passages`` and every path key are copied unchanged, so the request each arm
receives and the noise-subset exclusion are Addendum C's. The frozen ``noise.json`` must be
copied into both ``out_dir``s by the operator (``decide``'s preflight checks the margins
against it); the question-side files (``questions.jsonl``, ``audit.jsonl``, ``sample.jsonl``,
``passages*.jsonl``, ``skips.jsonl``, ``pending.jsonl``) likewise, and the retrieval ledgers of
the three decision arms are copied from the reporting ``out_dir`` after ``retrieve`` so both
``score`` runs read the same rows.

``--public-diff`` writes the key-by-key diff spec (no host, no path: ``out_dir`` is shown as
its basename) for the public README.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

SETTINGS = ("a", "d", "b", "c")
ARMS_DECISION = [
    {"name": "ft512o64", "collection_id": "oa-eval-ft512o64", "cost_class": "cheap", "baseline": True, "cost_rank": 1},
    {"name": "S", "collection_id": "oa-eval-s", "cost_class": "cheap", "baseline": False, "cost_rank": 2},
    {"name": "ft512o64s", "collection_id": "oa-eval-ft512o64s", "cost_class": "cheap", "baseline": False, "cost_rank": 3},
]
ARMS_REPORTING = ARMS_DECISION + [
    {"name": "S-regex", "collection_id": "oa-eval-sregex", "cost_class": "cheap", "baseline": False, "cost_rank": 4},
    {"name": "sent512s", "collection_id": "oa-eval-sent512s", "cost_class": "cheap", "baseline": False, "cost_rank": 5},
]
DECIDE = {"k": 5, "min_stratum_n": 30, "stratum_correction": "bonferroni", "tie_break": "paired_ladder"}


def derive(src: dict[str, Any], setting: str, kind: str, out_root: str, src_sha: str,
           collection_s: str = "oa-eval-s", collection_sregex: str = "oa-eval-sregex") -> dict[str, Any]:
    if kind not in ("decision", "reporting"):
        raise ValueError(kind)
    cfg = copy.deepcopy({k: v for k, v in src.items() if not k.startswith("_")})
    for k in ("decide", "margins", "score", "api", "arms"):
        if k not in src:
            raise ValueError(f"({setting}) Addendum C config lacks {k!r}")
    if {k: src["decide"].get(k) for k in DECIDE} != DECIDE:
        raise ValueError(f"({setting}) Addendum C decide block {src['decide']} != {DECIDE}")
    if not src["score"].get("exclude_noise_subset"):
        raise ValueError(f"({setting}) Addendum C config does not exclude the noise subset")
    arms = copy.deepcopy(ARMS_DECISION if kind == "decision" else ARMS_REPORTING)
    for a in arms:
        if a["name"] == "S":
            a["collection_id"] = collection_s
        if a["name"] == "S-regex":
            a["collection_id"] = collection_sregex
    cfg["arms"] = arms
    cfg["decide"] = dict(DECIDE)
    tag = "dec" if kind == "decision" else "rep"
    cfg["out_dir"] = str(Path(out_root) / f"out-h2h-{tag}-{setting}")
    cfg["_comment"] = (f"adopted-spec-h2h-2026-11 {kind} config, setting ({setting}): "
                       + ("three arms, Order B integer ranks (ft512o64 1, S 2, ft512o64s 3), run through score and decide"
                          if kind == "decision" else
                          "five arms, run through score only (ranks unused); S-regex and sent512s are in no decision config")
                       + "; every other key is Addendum C's.")
    cfg["_derived_from"] = {"addendum_c_config_sha256": src_sha, "setting": setting}
    return cfg


def public_diff(src: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Keys that differ, with path-like values reduced to a basename."""
    def flat(o: Any, p: str = "") -> dict[str, Any]:
        if isinstance(o, dict):
            out: dict[str, Any] = {}
            for k, v in o.items():
                out.update(flat(v, f"{p}/{k}"))
            return out
        return {p: o}
    a = flat({k: v for k, v in src.items() if not k.startswith("_")})
    b = flat({k: v for k, v in cfg.items() if not k.startswith("_")})
    diff = {}
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            va, vb = a.get(k), b.get(k)
            if k == "/out_dir":
                va, vb = "<addendum-c out_dir>", Path(str(vb)).name
            diff[k] = {"addendum_c": va, "h2h": vb}
    return diff


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--addendum-c", nargs="+", required=True, metavar="SETTING=PATH")
    ap.add_argument("--out-root", required=True, help="the directory the configs' out_dirs are named under (a path "
                    "written INTO the configs; nothing is created there)")
    ap.add_argument("--config-dir", help="where to write <setting>/{decision,reporting}.config.json (default: --out-root)")
    ap.add_argument("--collection-s", default="oa-eval-s")
    ap.add_argument("--collection-sregex", default="oa-eval-sregex")
    ap.add_argument("--public-diff", help="write the key-by-key diff spec (no hosts, no paths) here")
    args = ap.parse_args(argv)
    srcs = dict(kv.split("=", 1) for kv in args.addendum_c)
    unknown = set(srcs) - set(SETTINGS)
    if unknown:
        raise SystemExit(f"unknown settings {sorted(unknown)}")
    diffs: dict[str, Any] = {"schema": "h2h-config-diff/1", "note": "keys that differ from the Addendum C config of the "
                             "same setting; everything else is copied unchanged", "settings": {}}
    hashes = {}
    for setting, path in srcs.items():
        raw = Path(path).read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        src = json.loads(raw.decode("utf-8"))
        d = Path(args.config_dir or args.out_root) / setting
        d.mkdir(parents=True, exist_ok=True)
        diffs["settings"][setting] = {"addendum_c_config_sha256": sha}
        for kind in ("decision", "reporting"):
            cfg = derive(src, setting, kind, args.out_root, sha, args.collection_s, args.collection_sregex)
            out = d / f"{kind}.config.json"
            text = json.dumps(cfg, indent=1) + "\n"
            out.write_text(text, encoding="utf-8")
            hashes[f"{setting}/{kind}"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
            diffs["settings"][setting][kind] = {"sha256": hashes[f"{setting}/{kind}"], "diff": public_diff(src, cfg)}
    if args.public_diff:
        Path(args.public_diff).write_text(json.dumps(diffs, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(hashes, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
