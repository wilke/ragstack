#!/usr/bin/env python
"""Export the public, redacted artifacts of part 4 (confirmatory) and its addendum Q.

Reads the PRIVATE results of the part-4 run (``results-part4.json``), addendum Q
(``results-part4-addendum-q.json``) and, when present, the part-4 power table
from the trial's out-dir (named in the private config, ``--private-config`` or
env ``CHUNKTRIAL_PRIVATE_CONFIG``), and writes counts, rates, test results,
kappas, token statistics, gateway usage totals and per-record rows reduced to
record ids, public strata, salted doc hashes, labels, flags and numbers.

Redaction reuses :mod:`chunktrial_export_public` (its :class:`Redactor`, ``SAFE``
string rule, private-term check, six-word-run check and refuse-on-violation):

1. only named sections are exported; named sub-keys known to carry text or
   locations (prompts, hosts, URLs, file names, warnings) are not descended into;
2. every surviving string, key or value, must be ``SAFE`` (short token) and free
   of private terms; a few strings the harness writes itself (the test
   description, the reading-rule summaries) are allowed by exact value or
   pattern in :data:`VERBATIM` / :data:`VERBATIM_PATTERNS`, still subject to the
   private-term check. Doc ids become ``d:`` + 12 hex (salted sha256), private
   stratum names their public labels;
3. every output file is checked as a whole: no private question, quote, seed or
   prompt text (normalised substring or any six-word run) and no UUID in any
   letter case. Any violation stops the export with the offending paths: it
   never writes a partial set.

Run: ``PYTHONPATH=python python python/scripts/eval/part4_export_public.py``
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import chunktrial_export_public as cx  # noqa: E402

REPO = HERE.parents[2]
DEFAULT_DEST = REPO / "docs/plans/results/chunk-size-deployed-docs-part4-2026-10-25/artifacts"
SCHEMA = "part4-public-export/1"

#: Keys never exported, at any depth (text, locations, file names, host, free-form warnings).
SKIP_KEYS = frozenset({"url", "base_url", "gen_url", "judge_url", "gateway_url", "es_url", "host", "hosts",
                       "prompt", "prompts", "raw", "text", "warnings",
                       "file", "filename", "segmentation", "producer", "installed_distribution", "image"})
#: The provenance fields a public export carries (host and warnings are context, and a host can be private).
PROVENANCE_KEYS = ("commit", "describe", "dirty", "untracked", "citable", "recorded_at", "version")

PART4_KEYS = ("design", "n", "by_stratum", "index", "manifest_drift_docs", "manipulation_check",
              "size_distributions", "realised_tokens", "finish_length", "grade_rates", "retest",
              "reader_failures", "generation", "grader_usage", "analysis")

#: Exact strings the harness itself writes (deployed_docs_part4 / part4_addendum_q), allowed past ``SAFE``.
VERBATIM = frozenset({
    "page sign-flip permutation (standard p; exact when <= 20 non-zero pages), "
    "page-cluster bootstrap percentile CI, McNemar descriptive",
    "significant under both readers (intersection-union)",
    "retrieval: every gold quote in the delivered context; reader-independent",
    "Nothing here changes a part-4 confirmatory verdict.",
    "robust: every hypothesis keeps its confirmatory verdict",
    "not read: full set does not reproduce part 4",
    "secondary: same tests, outside the confirmatory family, unadjusted",
    "no questions in this contrast",
    "power of one test; under fixed_sequence a later hypothesis is tested only when every "
    "earlier one passed, so its power is at most the product along the sequence",
    "gold[0]", "gold[1]",
})
#: Harness-written strings with a variable part.
VERBATIM_PATTERNS = (
    re.compile(r"not robust: H\d (?:changed|unresolved on the clean set)"
               r"(?:, H\d (?:changed|unresolved on the clean set))*"),
)
UUID_ANY_CASE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

PER_RECORD_COND = ("found", "chunks", "ctx_tokens", "shortfall", "truncated", "truncated_empty")
PER_RECORD_READER = ("ok", "finish", "dropped")


class Redactor(cx.Redactor):
    """:class:`chunktrial_export_public.Redactor` with this export's skip keys, the harness's
    own verbatim strings, and the private stratum names and every configured host as terms."""

    def __init__(self, cfg: dict[str, Any], doc_ids: set[str], filenames: set[str], texts: list[str]):
        super().__init__(cfg, doc_ids, filenames, texts, skip_keys=SKIP_KEYS)
        extra = {k.lower() for k, v in cfg["public_stratum_labels"].items() if k != v}
        for key in ("gen_url", "judge_url", "gateway_url", "es_url"):
            u = cfg.get(key)
            if isinstance(u, str) and u:
                extra.add(u.split("//", 1)[-1].split("/")[0].split(":")[0].lower())
        self.terms = sorted((set(self.terms) | extra) - {"", "127.0.0.1", "localhost"})

    def check(self, s: str, path: str) -> None:
        if s in VERBATIM or any(p.fullmatch(s) for p in VERBATIM_PATTERNS):
            if any(t in s.lower() for t in self.terms):
                self.errors.append(f"{path}: private term in {s[:40]!r}")
            return
        super().check(s, path)


# ----------------------------------------------------------------------- inputs

def private_texts(cfg: dict[str, Any], qrows: list[dict[str, Any]]) -> list[str]:
    """Every private text an export must not contain: questions, quotes, seeds, prompts."""
    texts = [r.get(k) or "" for r in qrows for k in ("question", "answer_quote", "quote_a", "quote_b")]
    texts += [g for r in qrows for g in (r.get("gold") or []) if isinstance(g, str)]
    seeds = cfg.get("seed_questions") or {}
    texts += [v[0] if isinstance(v, list) else v for grp in seeds.values() for v in grp.values()]
    texts += [p for p in (cfg.get("prompts") or {}).values() if isinstance(p, str)]
    return texts


def load_question_rows(cfg: dict[str, Any], src: Path) -> list[dict[str, Any]]:
    p4 = cfg.get("part4") or {}
    qname = (p4.get("files") or {}).get("questions") or "part4-questions.jsonl"
    files = [src / qname] + [Path(f) if Path(f).is_absolute() else src / f for f in p4.get("prior_question_files") or []]
    rows: list[dict[str, Any]] = []
    for f in files:
        rows += cx.read_jsonl(f)
    return rows


def doc_ids_of(src: Path, qrows: list[dict[str, Any]], res: dict[str, Any]) -> set[str]:
    ids = {r["doc_id"] for r in qrows if r.get("doc_id")}
    ids |= {q["doc_id"] for q in (res.get("questions") or {}).values() if q.get("doc_id")}
    man = src / "manifest-261023.json"
    if man.is_file():
        ids |= set(json.loads(man.read_text()))
    return ids


# ---------------------------------------------------------------------- outputs

def provenance(pv: dict[str, Any] | None) -> dict[str, Any]:
    pv = pv or {}
    return {k: pv.get(k) for k in PROVENANCE_KEYS}


def run_totals(segments: list[dict[str, Any]]) -> dict[str, int]:
    tot: Counter[str] = Counter()
    for s in segments or []:
        for k, v in (s.get("totals_since_resume") or {}).items():
            tot[k] += int(v)
    return dict(sorted(tot.items()))


def segments_public(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = ("step", "started", "ended", "end", "reason", "resumed", "totals_since_resume")
    return [{k: s.get(k) for k in keep if k in s} for s in segments or []]


def gen_fallback(gf: dict[str, Any], red: Redactor) -> dict[str, Any]:
    """Counts and medians; items reduced to record id, set, public stratum, salted doc hash, page tokens."""
    out = {k: v for k, v in gf.items() if k != "items"}
    out["n"] = len(gf.get("items") or [])
    out["items"] = [{"record_id": it.get("record_id"), "set": it.get("set"),
                     "stratum": red.labels.get(it.get("stratum"), it.get("stratum")),
                     "doc": red.h(it["doc_id"]) if it.get("doc_id") else None,
                     "page_tokens": it.get("page_tokens"), "gen": it.get("gen")} for it in gf.get("items") or []]
    return out


def part4_summary(res: dict[str, Any], power: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {"schema": res.get("schema"), "status": "CONFIRMATORY",
                           "prereg_sha256": (res.get("prereg") or {}).get("sha256"),
                           "config_sha256": res.get("config_sha256"),
                           "citable": res.get("citable"), "provenance": provenance(res.get("provenance")),
                           "run_segments": segments_public(res.get("run_segments") or []),
                           "run_totals": run_totals(res.get("run_segments") or [])}
    for k in PART4_KEYS:
        if k in res:
            out[k] = res[k]
    out["missing_sections"] = [k for k in PART4_KEYS if k not in res]
    if power is not None:
        out["power_table"] = power
    return out


def per_record(res: dict[str, Any], red: Redactor) -> dict[str, Any]:
    qs, pi = res.get("questions") or {}, res.get("per_item") or {}
    legend = {
        "records": "record id -> {set, stratum (public), doc (salted hash), page_tokens, gen, arms}",
        "arms": "arm -> condition -> [" + ", ".join(PER_RECORD_COND) + ", readers]",
        "readers": "reader -> [" + ", ".join(PER_RECORD_READER) + ", grades: grader tag -> label or null]",
    }
    recs: dict[str, Any] = {}
    for rid in sorted(set(qs) | set(pi)):
        q = qs.get(rid) or {}
        arms: dict[str, Any] = {}
        for arm, conds in sorted((pi.get(rid) or {}).items()):
            arms[arm] = {}
            for cond, c in sorted(conds.items()):
                readers = {}
                for rd, r in sorted((c.get("readers") or {}).items()):
                    grades = {k: v for k, v in sorted(r.items())
                              if k not in PER_RECORD_READER and k != "reader_prompt_tokens"}
                    readers[rd] = [r.get(k) for k in PER_RECORD_READER] + [grades]
                arms[arm][cond] = [c.get(k) for k in PER_RECORD_COND] + [readers]
        recs[rid] = {"set": q.get("set"), "stratum": red.labels.get(q.get("stratum"), q.get("stratum")),
                     "doc": red.h(q["doc_id"]) if q.get("doc_id") else None,
                     "page_tokens": q.get("page_tokens"), "gen": q.get("gen"), "arms": arms}
    return {"legend": legend, "records": recs}


def addq_summary(aq: dict[str, Any]) -> dict[str, Any]:
    design = {k: v for k, v in (aq.get("design") or {}).items() if k != "prompt"}
    return {"schema": aq.get("schema"), "status": aq.get("status"), "note": aq.get("note"),
            "prereg_sha256": (aq.get("prereg") or {}).get("sha256"), "config_sha256": aq.get("config_sha256"),
            "inputs_sha256": aq.get("inputs_sha256"), "citable": aq.get("citable"),
            "provenance": provenance(aq.get("provenance")),
            "run_segments": segments_public(aq.get("run_segments") or []),
            "run_totals": run_totals(aq.get("run_segments") or []),
            "design": design, "analysis": aq.get("analysis")}


def gateway_usage(res: dict[str, Any], aq: dict[str, Any]) -> dict[str, Any]:
    def block(tot: dict[str, int]) -> dict[str, Any]:
        return {"calls": tot.get("gateway_calls", 0), "input_tokens": tot.get("gateway_prompt_tokens", 0),
                "output_tokens": tot.get("gateway_completion_tokens", 0),
                "failures": {k.split(":", 1)[1]: v for k, v in tot.items() if k.startswith("gateway_failures:")},
                "local": {k[len("local_"):]: v for k, v in tot.items() if k.startswith("local_")}}
    p4tot, aqtot = run_totals(res.get("run_segments") or []), run_totals(aq.get("run_segments") or [])
    gen = (res.get("generation") or {}).get("usage") or {}
    return {
        "source": "sum of run_segments[].totals_since_resume (the run's own gateway counters); "
                  "local = the readers' local-model calls, not gateway",
        "part4": block(p4tot) | {"recorded_by_store": {"generation": gen, "graders": res.get("grader_usage")}},
        "addendum_Q": block(aqtot) | {"recorded_by_store": (aq.get("analysis") or {}).get("usage")},
        "total": {k: block(p4tot)[k] + block(aqtot)[k] for k in ("calls", "input_tokens", "output_tokens")},
    }


# ------------------------------------------------------------------------ export

def build(cfg: dict[str, Any], src: Path) -> tuple[dict[str, Any], Redactor, dict[str, Path]]:
    inputs = {"results_part4": src / "results-part4.json", "results_addq": src / "results-part4-addendum-q.json"}
    power_p = src / "power-table-part4.json"
    res = json.loads(inputs["results_part4"].read_text())
    aq = json.loads(inputs["results_addq"].read_text())
    power = json.loads(power_p.read_text()) if power_p.is_file() else None
    if power is not None:
        inputs["power_table"] = power_p
    qrows = load_question_rows(cfg, src)
    p4f = (cfg.get("part4") or {}).get("files") or {}
    inputs["questions"] = src / (p4f.get("questions") or "part4-questions.jsonl")
    filenames = {r["filename"] for r in qrows if r.get("filename")}
    filenames |= {f for v in ((cfg.get("seed_questions") or {}).get("P") or {}).values()
                  if isinstance(v, list) and len(v) > 1 for f in v[1]}
    red = Redactor(cfg, doc_ids_of(src, qrows, res), filenames, private_texts(cfg, qrows))

    outputs: dict[str, Any] = {}
    summ = part4_summary(res, power)
    if "gen_fallback" in res:
        summ["gen_fallback"] = gen_fallback(res["gen_fallback"], red)
    outputs["part4_summary.json"] = red.clean(summ, "part4_summary")
    pr = per_record(res, red)
    outputs["part4_per_record.json"] = red.clean({"records": pr["records"]}, "part4_per_record") | {
        "legend": pr["legend"]}
    outputs["addq_summary.json"] = red.clean(addq_summary(aq), "addq_summary")
    gu = gateway_usage(res, aq)
    outputs["gateway_usage_part4.json"] = red.clean({k: v for k, v in gu.items() if k != "source"},
                                                    "gateway_usage") | {"source": gu["source"]}
    return outputs, red, inputs


def dumps_records(o: dict[str, Any]) -> str:
    """The per-record file: the legend indented, then one compact line per record (diffable, small)."""
    head = json.dumps(o["legend"], indent=1, sort_keys=True, ensure_ascii=True)
    rows = [f" {json.dumps(k)}: {json.dumps(v, sort_keys=True, ensure_ascii=True, separators=(',', ':'))}"
            for k, v in sorted(o["records"].items())]
    return '{"legend": ' + head + ',\n"records": {\n' + ",\n".join(rows) + "\n}}\n"


def term_in(term: str, blob: str) -> bool:
    """A private term in a whole file: a substring (case-insensitive), except that an all-digit
    term must stand alone (a port number is not a leak inside another number's digits)."""
    if term.isdigit():
        return re.search(rf"(?<![0-9.]){term}(?![0-9])", blob) is not None
    return term in blob.lower()


def blob_problems(name: str, blob: str, red: Redactor) -> list[str]:
    out = []
    why = cx.blob_leak(name, blob, red)
    if why:
        out.append(why)
    n_uuid = len(UUID_ANY_CASE.findall(blob))
    if n_uuid:
        out.append(f"{name} contains {n_uuid} UUID-shaped string(s)")
    hits = sum(1 for t in red.terms if term_in(t, blob))
    if hits:
        out.append(f"{name} contains {hits} private term(s)")
    return out


def export(cfg: dict[str, Any], src: Path, dest: Path, private_config: Path | None = None) -> int:
    outputs, red, inputs = build(cfg, src)
    if red.errors:
        print(f"REFUSING to write: {len(red.errors)} redaction errors", file=sys.stderr)
        for e in red.errors[:60]:
            print("  " + e, file=sys.stderr)
        return 2
    blobs = {n: (dumps_records(o) if n == "part4_per_record.json" else
                 json.dumps(o, indent=1, sort_keys=True, ensure_ascii=True) + "\n") for n, o in outputs.items()}
    problems = [p for n, b in blobs.items() for p in blob_problems(n, b, red)]
    if problems:
        print("REFUSING to write: " + "; ".join(problems), file=sys.stderr)
        return 3

    from ragstack.provenance import experiment_provenance

    if private_config is not None:
        inputs["private_config"] = private_config
    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "status": {"part4": "CONFIRMATORY", "addendum_Q": "EXPLORATORY"},
        "export_provenance": {k: v for k, v in experiment_provenance(repo=str(REPO)).items()
                              if k not in ("host", "warnings")},
        "inputs_sha256": {k: cx.sha_file(p) for k, p in sorted(inputs.items())},
        "files": {},
        "redaction": {
            "string_rule": cx.SAFE.pattern, "skipped_keys": sorted(SKIP_KEYS),
            "verbatim_harness_strings": len(VERBATIM) + len(VERBATIM_PATTERNS),
            "doc_ids": "salted sha256, 12 hex (salt private)",
            "stratum_labels": sorted(set(cfg["public_stratum_labels"].values())),
            "provenance_fields": list(PROVENANCE_KEYS),
            "dropped": ["question / quote / answer / chunk text", "prompt text (prompt_sha256 kept)",
                        "prereg and input file names (sha256 kept)", "host and provenance warnings",
                        "segmentation provenance", "subsets (record-id lists and retest hash prefixes)",
                        "per-item reader prompt token counts"],
            "checks": ["SAFE string rule on every key and value", "private terms (config) in every string",
                       "private texts: normalised substring and any 6-word run, per file",
                       "UUID-shaped strings (any letter case), per file"],
        },
    }
    for n, b in blobs.items():
        manifest["files"][n] = hashlib.sha256(b.encode()).hexdigest()
    mblob = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    problems = blob_problems("export_manifest.json", mblob, red)
    if problems:
        print("REFUSING to write: " + "; ".join(problems), file=sys.stderr)
        return 3
    dest.mkdir(parents=True, exist_ok=True)
    for n, b in blobs.items():
        (dest / n).write_text(b)
    (dest / "export_manifest.json").write_text(mblob)
    shown = dest.relative_to(REPO) if dest.resolve().is_relative_to(REPO) else dest
    print(json.dumps({"wrote": sorted([*blobs, "export_manifest.json"]), "dest": str(shown)}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--private-config", default=os.environ.get("CHUNKTRIAL_PRIVATE_CONFIG"))
    ap.add_argument("--src-dir", default=None, help="default: the private config's out_dir")
    ap.add_argument("--dest", default=str(DEFAULT_DEST))
    args = ap.parse_args(argv)
    if not args.private_config:
        raise SystemExit("a private config is required: pass --private-config or set CHUNKTRIAL_PRIVATE_CONFIG")
    cfg_path = Path(args.private_config)
    cfg = json.loads(cfg_path.read_text())
    return export(cfg, Path(args.src_dir or cfg["out_dir"]), Path(args.dest), cfg_path)


if __name__ == "__main__":
    sys.exit(main())
