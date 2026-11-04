"""Alias study analysis inputs (prereg §7 R6, §4.3 items 4-6, O10; DEVIATIONS D-1): the read
guard, the tercile rule, the pairwise cosine, the sources and added chunks, the sensitivity sets,
and an end-to-end build on a synthetic study directory that never opens an outcome file. No /rag
path."""
from __future__ import annotations

import builtins
import io
import json
import math
import os
import stat
import sys
import zlib
from pathlib import Path
from typing import Any

import numpy as np
import pytest

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

import alias_study_common as asc  # noqa: E402
import alias_study_inputs as ai  # noqa: E402
import alias_study_stats as ast  # noqa: E402

DIM = 6


# ------------------------------------------------------------------ pure pieces

def test_denied_prefixes() -> None:
    for rel in ("main/judge", "main/judge/final.jsonl", "main/analysis-A/results.json", "main/analysis-inputs/x",
                "main/runs/base/x.jsonl", "spot/judge/sheet.json", "pilot/judge/final.jsonl", "pilot/report/r.json"):
        assert ai.denied(rel) is not None, rel
    for rel in ("main/judgement.txt", "spotless/x", "corpus/manifests/counts-v4.json", "out/main/accepted-alias-O.jsonl"):
        assert ai.denied(rel) is None, rel


def test_denied_names_anywhere() -> None:
    for rel in ("corpus/manifests/final.jsonl", "calib/labels-qwen.jsonl", "out/x/judge-summary.json",
                "ops/hits.jsonl", "a/b.owner-labels.jsonl", "docs/stage-a-commit.public.json", "x/results-stage-A.json"):
        assert ai.denied(rel) is not None, rel
    assert ai.denied("calib/pairs.jsonl") is None


def test_guard_refuses_denied_even_when_listed(tmp_path: Path) -> None:
    d = tmp_path / "study"
    bad = [d / "main/judge/final.jsonl", d / "main/analysis-A/results.json", d / "spot/records/labels.jsonl",
           d / "main/judge/renamed.jsonl"]
    ok = d / "corpus/manifests/counts-v4.json"
    for p in [*bad, ok]:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
    base = tmp_path / "base.jsonl"
    base.write_text("")
    g = ai.Guard(d, base, [*bad, ok])
    for p in bad:
        with pytest.raises(ai.GuardError, match="refusing"):
            g.check(p)
    assert g.check(ok) == ok
    with pytest.raises(ai.GuardError, match="not an allow-listed"):
        g.check(d / "corpus/manifests/other.json")


def test_guard_refuses_a_renamed_outcome_file_in_an_allowed_directory(tmp_path: Path) -> None:
    d = tmp_path / "study"
    p = d / "corpus/manifests/judge-summary.json"
    p.parent.mkdir(parents=True)
    p.write_text("{}")
    base = tmp_path / "base.jsonl"
    base.write_text("")
    with pytest.raises(ai.GuardError, match="outcome file name"):
        ai.Guard(d, base, [p]).check(p)


def test_guard_refuses_symlinks(tmp_path: Path) -> None:
    d = tmp_path / "study"
    (d / "main/judge").mkdir(parents=True)
    (d / "main/judge/x.jsonl").write_text("{}")
    outside = tmp_path / "elsewhere.jsonl"
    outside.write_text("{}")
    base = tmp_path / "base.jsonl"
    base.write_text("")
    # a listed name that is a link: into main/judge, and to a file outside the root
    for target in (d / "main/judge/x.jsonl", outside):
        link = d / "calib/pairs.jsonl"
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            link.unlink()
        link.symlink_to(target)
        with pytest.raises(ai.GuardError, match="symlink"):
            ai.Guard(d, base, [link]).check(link)
    # a linked parent directory
    real = tmp_path / "realdir"
    real.mkdir()
    (real / "counts-v4.json").write_text("{}")
    (d / "corpus").mkdir()
    (d / "corpus/manifests").symlink_to(real)
    p = d / "corpus/manifests/counts-v4.json"
    with pytest.raises(ai.GuardError, match="symlink"):
        ai.Guard(d, base, [p]).check(p)


def test_tercile_cuts_inverse_cdf() -> None:
    assert ai.tercile_cuts(range(1, 10)) == [3.0, 6.0]
    assert ai.tercile_sizes(range(1, 10), [3, 6]) == {"T1": 3, "T2": 3, "T3": 3}
    v = [5, 1, 4, 2, 3, 7, 6, 9, 8, 10]          # n = 10: v_(4), v_(7)
    assert ai.tercile_cuts(v) == [4.0, 7.0]
    ties = [0, 0, 0, 0, 1, 2]                      # n = 6: v_(2) = 0, v_(4) = 0
    assert ai.tercile_cuts(ties) == [0.0, 0.0]
    assert ai.tercile_sizes(ties, [0, 0]) == {"T1": 4, "T2": 0, "T3": 2}
    # the statistics' own reading agrees with the sizes
    cuts = ai.tercile_cuts(v)
    assert sum(ast.tercile_of(x, cuts) == "T1" for x in v) == ai.tercile_sizes(v, cuts)["T1"]
    with pytest.raises(ValueError):
        ai.tercile_cuts([1, 2])


def test_mean_pair_cosine_is_the_brute_force_mean() -> None:
    rng = np.random.default_rng(0)
    a, b = rng.normal(size=(4, DIM)), rng.normal(size=(7, DIM))
    ua = a / np.linalg.norm(a, axis=1, keepdims=True)
    ub = b / np.linalg.norm(b, axis=1, keepdims=True)
    brute = float(np.mean([[x @ y for y in ub] for x in ua]))
    assert ai.mean_pair_cosine(ua.sum(0), 4, ub.sum(0), 7) == pytest.approx(brute, abs=1e-12)
    assert ai.mean_pair_cosine(ua.sum(0), 4, np.zeros(DIM), 0) is None


def test_work_key_and_source() -> None:
    works = {"W1": {"pmcid": "PMC1"}, "W2": {"pmcid": None}, "W3": {"pmcid": "PMC3"}}
    have = {"PMC1", "W2", "W3"}
    assert ai.work_key("W1", works, have) == "PMC1" and ai.source_of("W1", "PMC1", works) == "jats"
    assert ai.work_key("W2", works, have) == "W2" and ai.source_of("W2", "W2", works) == "pdf"
    # a work with a PMC id that was taken from its PDF is keyed by its work id
    assert ai.work_key("W3", works, have) == "W3" and ai.source_of("W3", "W3", works) == "pdf"
    with pytest.raises(SystemExit):
        ai.work_key("W1", works, {"PMC1", "W1"})
    with pytest.raises(SystemExit):
        ai.work_key("W9", works, have)


# ------------------------------------------------------------------ a synthetic study

def S(i: int) -> str:
    return f"PMCSTUDY{i:04d}"


def jl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def rec(paper: str, k: str, kind: str, alias: str) -> dict[str, Any]:
    return {"record_id": f"alias-O-{paper.lower()}-{k}", "paper": paper, "kind": kind, "stratum": kind,
            "question": f"What does {paper} report about {alias}?", "primary": f"primary {alias}", "alias": alias,
            "alias_kind": "abbreviation", "gold_chunks": [f"g-{paper}-{k}"], "verifier": "claude"}


def chunk(cid: str, paper: str, text: str = "x") -> dict[str, Any]:
    return {"chunk_id": cid, "doc_id": f"doc-{paper}", "content": f"{text}   {cid}",
            "metadata": {"source_path": paper, "title": "t"}}


def vec(seed: str) -> list[float]:
    r = np.random.default_rng(zlib.crc32(seed.encode()))
    return [float(x) for x in r.normal(size=DIM) + 0.5]


@pytest.fixture()
def study(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Study papers 0-5 (2 G + 1 C), held-out paper H. Neighbours: S0 two (one JATS, one PDF),
    S1-S4 one JATS each, S5 none. Random draws match the counts, all JATS except one PDF take of
    S1 in draw 1."""
    d = tmp_path / "study"
    recs = []
    for i in range(6):
        recs += [rec(S(i), "g1", "G", f"A{i}"), rec(S(i), "g2", "G", f"B{i}"), rec(S(i), "c", "C", f"C{i}")]
    recs += [rec("PMCHELD", "g1", "G", "H"), rec("PMCHELD", "c", "C", "HC")]
    jl(d / ai.LAYOUT["records"], recs)
    jl(d / ai.LAYOUT["main_drafts"], [{"record_id": r["record_id"], "paper": r["paper"]} for r in recs]
       + [{"record_id": "alias-O-unaccepted", "paper": S(0)}])
    jl(d / ai.LAYOUT["calib_pairs"], [{"record_id": rec(S(0), "c", "C", "")["record_id"], "q_paper": S(0)},
                                      {"record_id": "alias-O-pmcheld-c", "q_paper": "PMCHELD"},
                                      {"record_id": "alias-P-not-in-main", "q_paper": S(1)}])
    jl(d / ai.LAYOUT["pilot1_drafts"], [{"record_id": "p1", "paper": S(1)}, {"record_id": "p2", "paper": "PMCOTHER"}])
    jl(d / ai.LAYOUT["pilot2_drafts"], [{"record_id": "p3", "paper": S(1)}, {"record_id": "p4", "paper": "PMCHELD"}])
    jl(d / ai.LAYOUT["validation_drafts"], [{"record_id": "v1", "paper": S(2)}, {"record_id": "v2", "paper": "PMCHELD"}])

    study_works = {S(i): {"topic": f"T{i % 2}", "cited_by_count": [3, 0, 12, 7, 7, 40][i], "pmcid": S(i),
                          "wid": f"WS{i}"} for i in range(6)}
    attributed: dict[str, list[list[str]]] = {S(0): [["WN0", "ref"], ["WN1", "citer"]], S(5): []}
    nworks = {"WN0": {"pmcid": "PMCN0"}, "WN1": {"pmcid": None}}
    for i in range(1, 5):
        attributed[S(i)] = [[f"WN{i + 1}", "ref"]]
        nworks[f"WN{i + 1}"] = {"pmcid": f"PMCN{i + 1}"}
    per_study = {S(i): {"citer_list": {"cited_by_count": [3, 0, 12, 7, 9, 40][i]}} for i in range(6)}
    sel = {"study_works": study_works, "attributed": attributed, "works": nworks, "per_study": per_study}
    nkeys = {"WN0": "PMCN0", "WN1": "WN1", **{f"WN{i + 1}": f"PMCN{i + 1}" for i in range(1, 5)}}
    n_chunks = {"PMCN0": 3, "WN1": 2, "PMCN2": 4, "PMCN3": 1, "PMCN4": 2, "PMCN5": 5}
    draws: dict[str, Any] = {}
    r_chunks: dict[str, dict[str, int]] = {}
    for dn in (1, 2):
        works: dict[str, Any] = {}
        per: dict[str, Any] = {}
        rc: dict[str, int] = {}
        for i in range(6):
            took = []
            for j, _ in enumerate(attributed[S(i)]):
                wid = f"WR{dn}-{i}-{j}"
                pdf = dn == 1 and i == 1
                works[wid] = {"pmcid": None if pdf else f"PMCR{dn}{i}{j}"}
                took.append({"wid": wid, "source": "pdf" if pdf else "jats"})
                rc[wid if pdf else f"PMCR{dn}{i}{j}"] = 2 + j + dn
            per[S(i)] = {"took": took}
        draws[f"random{dn}"] = {"per_study": per, "works": works}
        r_chunks[f"random{dn}"] = rc
    chunks = {"neighbour": [chunk(f"n-{k}-{c}", k) for k, n in n_chunks.items() for c in range(n)]}
    for a in ("random1", "random2"):
        chunks[a] = [chunk(f"{a}-{k}-{c}", k) for k, n in r_chunks[a].items() for c in range(n)]
    for a, rows in chunks.items():
        jl(d / ai.LAYOUT[f"chunks_{a}"], rows)
    psc: dict[str, dict[str, int]] = {"neighbour": {}, "random-1": {}, "random-2": {}}
    for i in range(5):
        psc["neighbour"][S(i)] = sum(n_chunks[nkeys[w]] for w, _ in attributed[S(i)])
        for dn in (1, 2):
            psc[f"random-{dn}"][S(i)] = sum(r_chunks[f"random{dn}"][k] for k in r_chunks[f"random{dn}"]
                                            if f"-{i}-" in k or k.startswith(f"PMCR{dn}{i}"))
    man = d / "corpus/manifests"
    for name, obj in (("neighbour-selection-v4.json", sel), ("random-1-draw-v4.json", draws["random1"]),
                      ("random-2-draw-v4.json", draws["random2"]), ("per-study-chunks-v4.json", psc)):
        man.mkdir(parents=True, exist_ok=True)
        (man / name).write_text(json.dumps(obj))
    counts: dict[str, Any] = {"manifest_sha256": {n: asc.sha256_file(man / n) for n in
                                                  ("neighbour-selection-v4.json", "random-1-draw-v4.json",
                                                   "random-2-draw-v4.json", "per-study-chunks-v4.json")}}
    pdf = {"neighbour": 1, "random1": 1, "random2": 0}
    for a, ca in ai.ARMS.items():
        papers = {r["metadata"]["source_path"] for r in chunks[a]}
        counts[ca] = {"chunks_sha256": asc.sha256_file(d / ai.LAYOUT[f"chunks_{a}"]), "papers": len(papers),
                      "chunks": len(chunks[a]), "pdf_share": round(pdf[a] / len(papers), 4)}
    (man / "counts-v4.json").write_text(json.dumps(counts))

    base_rows = [chunk(f"b-{S(i)}-{c}", S(i)) for i in range(6) for c in range(2 + i % 3)]
    base_rows += [chunk(f"b-PMCBASE-{c}", "PMCBASE") for c in range(3)]
    base = tmp_path / "exports" / "chunks-oa.jsonl"
    jl(base, base_rows)
    vecs = {r["chunk_id"]: vec(r["chunk_id"]) for r in base_rows + chunks["neighbour"]}

    def emb_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"schema": "ragstack.embedding_file/v1", "dim": DIM}] + [
            {"id": r["chunk_id"], "doc_id": r["doc_id"], "content": r["content"], "embedding": vecs[r["chunk_id"]],
             "metadata": {"source_path": r["metadata"]["source_path"]}} for r in rows]
    eb, en = d / ai.EMB_DIRS["base"], d / ai.EMB_DIRS["neighbour"]
    jl(eb / "oa.emb.jsonl", emb_rows(base_rows))
    half = len(chunks["neighbour"]) // 2
    jl(en / "n-b000.emb.jsonl", emb_rows(chunks["neighbour"][:half]))
    jl(en / "n-b001.emb.jsonl", emb_rows(chunks["neighbour"][half:]))
    for e in (eb, en):
        (e / "x.receipt.json").write_text("{}")
        (e / "SHA256SUMS").write_text("".join(f"{asc.sha256_file(f)}  {f.name}\n" for f in sorted(e.iterdir())))
    # decoys that must never be opened
    for rel in ("main/judge/final.jsonl", "main/analysis-A/results.json", "main/runs/base/rows.jsonl",
                "spot/records/labels.jsonl"):
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text("{}\n")

    monkeypatch.setattr(ai, "BASE_EXPORT_SHA256", asc.sha256_file(base))
    monkeypatch.setattr(ai, "EXPECT", {
        "study_papers": 6, "calibration_overlap_records": 1, "pilot_papers": 1, "validation_papers": 1,
        "validation_papers_balanced_records": 3, "neighbour_papers": 6, "random_papers": 6,
        "neighbour_jats": 5, "neighbour_pdf": 1, "study_papers_without_neighbours": 1})
    return {"dir": d, "base": base, "vecs": vecs, "base_rows": base_rows, "chunks": chunks, "nkeys": nkeys,
            "attributed": attributed, "n_chunks": n_chunks, "psc": psc}


def _build(st: dict[str, Any]) -> dict[str, Any]:
    return ai.build(st["dir"], st["base"], workers=1, verify_record_set=False)


def test_build_values(study: dict[str, Any]) -> None:
    res = _build(study)
    cov = res["covariates"]
    assert sorted(cov) == [S(i) for i in range(6)]
    assert res["sets"] == {"without_calibration_overlap": ["alias-O-pmcstudy0000-c"],
                           "without_pilot_papers": [S(1)], "without_validation_papers": [S(2)]}
    assert cov[S(0)]["topic"] == "T0" and cov[S(5)]["citation_count"] == 40
    # S0 has a PDF neighbour, S1 a PDF take in draw 1, S5 nothing added (vacuously XML-only)
    assert [cov[S(i)]["xml_only"] for i in range(6)] == [False, False, True, True, True, True]
    assert "added_chunks" not in cov[S(5)]                 # §7 R23: the 298-paper population
    assert res["summary"]["papers_with_added_chunks"] == 5
    assert cov[S(5)]["text_similarity"] is None
    assert cov[S(0)]["added_chunks"]["neighbour"] == 5
    for i in range(5):
        assert cov[S(i)]["added_chunks"]["neighbour"] == study["psc"]["neighbour"][S(i)]
    assert res["attribution"] == {"PMCN0": S(0), "WN1": S(0), "PMCN2": S(1), "PMCN3": S(2), "PMCN4": S(3),
                                  "PMCN5": S(4)}
    # text similarity: the brute-force mean over all chunk pairs
    vecs = study["vecs"]
    own = [vecs[r["chunk_id"]] for r in study["base_rows"] if r["metadata"]["source_path"] == S(0)]
    nb = [vecs[r["chunk_id"]] for r in study["chunks"]["neighbour"] if r["metadata"]["source_path"] in ("PMCN0", "WN1")]
    brute = float(np.mean([[np.dot(a, b) / np.linalg.norm(a) / np.linalg.norm(b) for b in nb] for a in own]))
    assert cov[S(0)]["text_similarity"] == pytest.approx(brute, abs=1e-12)
    assert res["cuts"]["citation_count"] == ai.tercile_cuts([3, 0, 12, 7, 7, 40]) == [3.0, 7.0]
    assert res["summary"]["papers_with_text_similarity"] == 5


@pytest.mark.parametrize("workers", [1, 2])
def test_build_reads_no_outcome_file(study: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                     workers: int) -> None:
    """Every file the build opens, in this process and (``workers=2``) in the pool's forked
    workers, which inherit the spy and append to a shared log, is an allow-listed input."""
    import multiprocessing as mp
    if workers > 1 and mp.get_start_method() != "fork":
        pytest.skip("the pool's workers inherit the spy only under fork")
    log = tmp_path / "opened.log"
    real = builtins.open

    def spy(file: Any, *a: Any, **k: Any) -> Any:
        if isinstance(file, (str, os.PathLike)) and str(file) != str(log):
            with real(log, "a", encoding="utf-8") as fh:
                fh.write(f"{os.getpid()}\t{os.path.abspath(file)}\n")
        return real(file, *a, **k)
    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(io, "open", spy)
    ai.build(study["dir"], study["base"], workers=workers, verify_record_set=False)
    monkeypatch.undo()
    rows = [x.split("\t", 1) for x in log.read_text(encoding="utf-8").splitlines()]
    root = str(study["dir"]) + os.sep
    under = [p for _, p in rows if p.startswith(root)]
    assert under, "the spy saw no read"
    assert not [p for p in under if ai.denied(p[len(root):])], "an outcome file was opened"
    allowed = {str(p) for p in ai.allowed_inputs(study["dir"])}
    allowed |= {str(p) for e in ai.EMB_DIRS.values() for p in (study["dir"] / e).glob("*.emb.jsonl")}
    assert set(under) <= allowed
    emb = {p for _, p in rows if p.endswith(".emb.jsonl")}
    assert emb, "the embedding files were not read"
    if workers > 1:
        assert {pid for pid, p in rows if p.endswith(".emb.jsonl")} - {str(os.getpid())}, \
            "the pool path was not exercised"


def test_a_decoy_outcome_file_in_an_allowed_directory_is_refused(study: dict[str, Any],
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """A layout entry pointed at a renamed judge-label file beside the calibration pairs (same
    shape, same directory): only the name deny list stands between it and the build."""
    d = study["dir"]
    pairs = (d / ai.LAYOUT["calib_pairs"]).read_text(encoding="utf-8")
    (d / "calib/labels-qwen.jsonl").write_text(pairs, encoding="utf-8")
    monkeypatch.setitem(ai.LAYOUT, "calib_pairs", "calib/labels-qwen.jsonl")
    with pytest.raises(ai.GuardError, match="outcome file name"):
        _build(study)


def test_open_choice_checks() -> None:
    cit = [0, 1, 2, 3, 4, 5, 6, 7, 8]
    cuts = {"citation_count": ai.tercile_cuts(cit), "text_similarity": ai.tercile_cuts(cit)}
    out = ai.open_choice_checks(cit, cit, cuts, cit)
    assert out["type7_citation_count"]["cuts"] == pytest.approx([2 + 2 / 3, 5 + 1 / 3])
    assert out["type7_citation_count"]["papers_changing_tercile"] == 0
    assert out["citer_list_citation_count"] == {"papers_differing": 0, "cuts": [2.0, 5.0], "papers_changing_tercile": 0}
    citer = [0, 1, 2, 3, 4, 5, 6, 7, 80]
    assert ai.open_choice_checks(cit, cit, cuts, citer)["citer_list_citation_count"]["papers_differing"] == 1
    ties = [0, 0, 0, 0, 1, 2]                      # type 1: [0, 0]; type 7: [0, 0.333..]
    c = {"citation_count": ai.tercile_cuts(ties), "text_similarity": ai.tercile_cuts(ties)}
    assert ai.open_choice_checks(ties, ties, c, ties)["type7_citation_count"]["papers_changing_tercile"] == 0


def test_count_mismatch_stops(study: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(ai.EXPECT, "pilot_papers", 2)
    with pytest.raises(SystemExit, match="pilot_papers"):
        _build(study)


def test_tampered_manifest_stops(study: dict[str, Any]) -> None:
    p = study["dir"] / ai.LAYOUT["per_study_chunks"]
    p.write_text(p.read_text().replace("}", " }", 1))
    with pytest.raises(SystemExit, match="sha256"):
        _build(study)


def test_write_all_and_the_statistics_read_it(study: dict[str, Any], tmp_path: Path) -> None:
    res = _build(study)
    prov = {"commit": "c" * 40, "dirty": False, "untracked": 0, "citable": True, "host": "h", "warnings": []}
    pub = tmp_path / "artifacts" / "tercile-cuts.json"
    rep = ai.write_all(res, study["dir"], pub, prov)
    out = Path(rep["out_dir"])
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    names = {p.name for p in out.iterdir()}
    assert names == {"paper-covariates.json", "neighbour-attribution.json", "tercile-cuts.json",
                     "without-calibration-overlap.txt", "without-pilot-papers.txt", "without-validation-papers.txt",
                     "stats-config-keys.json", "inputs-manifest.json", "SHA256SUMS"}
    for p in out.iterdir():
        assert stat.S_IMODE(p.stat().st_mode) == 0o600, p.name
    for line in (out / "SHA256SUMS").read_text().splitlines():
        sha, name = line.split()
        assert asc.sha256_file(out / name) == sha
    assert pub.read_bytes() == (out / "tercile-cuts.json").read_bytes()
    cuts = json.loads(pub.read_text())
    assert "host" not in cuts["provenance"] and cuts["citation_count"] == [3.0, 7.0]
    assert not asc.public_problems(cuts, res["forbidden"])
    # the statistics' readers
    keys = json.loads((out / "stats-config-keys.json").read_text())
    assert keys["tercile_cuts"] == ai.PUBLIC_CUTS_REL
    cfg = {"sensitivity_sets": keys["sensitivity_sets"]}
    recs = asc.load_records(study["dir"] / ai.LAYOUT["records"])
    bal, _ = asc.split_records(recs)
    assert len(ast._subset(bal, cfg, "without_calibration_overlap")) == len(bal) - 1
    assert len(ast._subset(bal, cfg, "without_pilot_papers")) == len(bal) - 3
    assert len(ast._subset(bal, cfg, "without_validation_papers")) == len(bal) - 3
    cov = json.loads((out / "paper-covariates.json").read_text())
    names_ = [S(i) for i in range(6)]
    _, k, no_topic = ast.topic_sums(names_, np.ones(6), {p: (cov.get(p) or {}).get("topic") for p in names_})
    assert (k, no_topic) == (2, 0)
    attr = json.loads((out / "neighbour-attribution.json").read_text())
    assert attr.get("WN1") == S(0) and attr["schema"] == ai.SCHEMA
    with pytest.raises(SystemExit, match="written once"):
        ai.write_all(res, study["dir"], None, prov)


def test_public_cut_file_refuses_an_id(study: dict[str, Any], tmp_path: Path) -> None:
    res = _build(study)
    with pytest.raises(SystemExit, match="§11"):
        ai.write_all(res, study["dir"], tmp_path / "c.json", {"leak": S(3), "citable": True})


def test_main_refuses_an_uncitable_tree(study: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai, "provenance", lambda: {"citable": False})
    with pytest.raises(SystemExit, match="citable"):
        ai.main(["--study-dir", str(study["dir"]), "--base-export", str(study["base"])])


def test_definitions_cover_every_output() -> None:
    for k in ("topic", "citation_count", "text_similarity", "xml_only", "added_chunks", "neighbour_attribution",
              "tercile_cuts", "without_calibration_overlap", "without_pilot_papers", "without_validation_papers"):
        assert ai.DEFINITIONS[k]
    assert math.isclose(ai._ratio([1.0, 3.0]), 1.4142, abs_tol=1e-4)
