"""Post-pilot fixes to the round-3 drafting pipeline (the papers-OC owner's format check).

* ``export-partner`` carries the verifier provenance (``verifier``, ``verifier_refused``,
  ``verifier_model``), so a Codex-verified record is distinguishable in the export;
* layer 1 rejects ``value_is_bound`` (an inequality governs a number of the VALUE; the
  quote is never read) except on the fields whose definition is a limit or a threshold;
* layer 1 flags ``empty_case`` in fields mode;
* ``export-partner`` / ``import-partner-verdicts`` refuse a path the run writes itself;
* ``import-partner-verdicts`` end to end on a verdict object shaped like the owner's pilot
  verdicts (5 sampled, 2 errors, a seed string, rejects with reasons, accepts without).

Synthetic corpora, fake gateway and fake CLIs; no store, no network, no model.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.eval import glossbench_r2_support as sup
from tests.eval import test_glossbench_r2_fields as r2f
from tests.eval import test_glossbench_r3_draft as r3d
from tests.eval import test_glossbench_r3_yield as r3y

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

import _glossbench_r2 as g  # noqa: E402
import glossbench_r2_checks as ck  # noqa: E402
import glossbench_r2_draft as dr  # noqa: E402
import glossbench_r2_fields as fl  # noqa: E402

run = r3d.run
jsonl = r3d.jsonl

# ------------------------------------------------------------------ value_is_bound: the function


@pytest.mark.parametrize("word", ck.BOUND_PHRASES)
def test_every_bound_word_governing_the_number_is_a_bound(word: str) -> None:
    assert ck.value_bound(f"{word} 5 m") == word
    assert ck.value_bound(f"{word.upper()} 0.25 m3/s") == word            # case-insensitive
    if len(word.split()) < 3:
        assert ck.value_bound(f"{word} the 5 m") == word                  # within the window
    assert ck.value_bound(f"{word} two people") == word                   # a number word is a number


@pytest.mark.parametrize("sym", [*ck.BOUND_SYMBOLS, "<=", ">="])
def test_every_bound_symbol_immediately_before_the_number_is_a_bound(sym: str) -> None:
    assert ck.value_bound(f"{sym}0.05") is not None
    assert ck.value_bound(f"p {sym} 0.05") is not None
    assert ck.value_bound(f"{sym} -3 °C") is not None                     # a sign between is allowed
    assert ck.value_bound(f"0.05 {sym} p") is None                         # after the number: not governing


@pytest.mark.parametrize("value", [
    "0.3–0.5 m/s", "0.3-0.5", "0.3 to 0.5 m/s", "between 2 and 4 h",     # ranges are not bounds
    "over the course of 2 h",                                           # "over" is 4 tokens before 2
    "5 m above the floor", "2 h overall", "underlying 3 m",             # after the number / not a whole word
    "about 2 cm2", "1.2 kg/m3", "twenty-one", "Two",
    "moreover 5 m", "thereunder 4", "overhead 3 m",
])
def test_not_a_bound(value: str) -> None:
    assert ck.value_bound(value) is None


def test_the_window_is_three_tokens() -> None:
    assert ck.value_bound("above a b 5") == "above"                       # 3 tokens before
    assert ck.value_bound("above a b c 5") is None                        # 4 tokens before
    assert ck.value_bound("no more than 5") == "no more than"
    assert ck.value_bound("at least x y 5") is None                       # "at least" ends 3 before; starts 4


# ------------------------------------------------------------------ value_is_bound / empty_case in layer 1

def _bound_paper(tmp: Path) -> tuple[g.Corpus, ck.PaperIndex, dict[str, fl.FieldSpec]]:
    text = ("Room study 1. " + sup.FILLER
            + "Particles of density lower than 1500 kg/m3 settled while the supply was 0.42 m3/s. " + sup.FILLER
            + "The outdoor-air supply stayed above 0.31 m3/s all day. " + sup.FILLER
            + "The tolerated risk was below 0.1 for the lesson. " + sup.FILLER)
    rows = [g.parse_chunk(r, "t") for r in sup.chunk_rows("d1", text, "PMC101")]
    c = g.build_corpus(rows, "doc_id")
    fields = [*r2f.FIELDS, {"field": "risk_tolerance", "unit": "", "definition": "tolerated expected number of "
                            "transmissions during the occupancy", "terms": ["risk tolerance", "tolerated risk"]}]
    fs = fl.read_field_list(r2f.write_fields(tmp / "f.json", fields))[0]
    return c, ck.PaperIndex(c, 200), {f.field: f for f in fs}


def _chunk(c: g.Corpus, text: str) -> g.ChunkRow:
    return next(x for x in c.paper_chunks("d1") if text in x.content)


def _l1(c: Any, idx: Any, d: dict[str, Any], chunk: g.ChunkRow, spec: fl.FieldSpec | None,
        **cfg: Any) -> dict[str, Any]:
    terms = list(spec.terms) if spec else ["supply"]
    return ck.layer1({**d, "binding_terms": terms}, None, stratum="V" if spec else "M_num", paper="d1",
                     source_chunk=chunk, idx=idx, cfg=cfg, field=spec)


QUOTE_BOUND = {"question": "What was the outdoor-air flow into the whole room while particles settled?",
               "quote": "Particles of density lower than 1500 kg/m3 settled while the supply was 0.42 m3/s",
               "quantity": "supply", "value": "0.42 m3/s", "symbol": "", "case": "while particles settled"}
VALUE_BOUND = {"question": "What was the outdoor-air flow into the whole room all day?",
               "quote": "The outdoor-air supply stayed above 0.31 m3/s all day", "quantity": "supply",
               "value": "above 0.31 m3/s", "symbol": "", "case": "all day"}


def test_a_bound_word_in_the_quote_but_not_in_the_value_passes(tmp_path: Path) -> None:
    c, idx, fs = _bound_paper(tmp_path)
    ch = _chunk(c, "lower than 1500")
    r = _l1(c, idx, QUOTE_BOUND, ch, fs["ventilation_flow_m3_s"])
    assert r["verdict"] == "pass", r["reasons"]
    r = _l1(c, idx, QUOTE_BOUND, ch, None)                                # open mode too
    assert "value_is_bound" not in r["reasons"], r["reasons"]


def test_a_bound_value_is_rejected_in_both_modes(tmp_path: Path) -> None:
    c, idx, fs = _bound_paper(tmp_path)
    ch = _chunk(c, "stayed above 0.31")
    r = _l1(c, idx, VALUE_BOUND, ch, fs["ventilation_flow_m3_s"])
    assert r["verdict"] == "reject" and r["reasons"] == ["value_is_bound"] and r["value_bound"] == "above"
    r = _l1(c, idx, VALUE_BOUND, ch, None)
    assert "value_is_bound" in r["reasons"]
    # the same record with the scalar as its value passes
    assert _l1(c, idx, {**VALUE_BOUND, "value": "0.31 m3/s"}, ch, fs["ventilation_flow_m3_s"])["verdict"] == "pass"
    # off: a run whose layer-1 log predates the rule
    assert "value_is_bound" not in _l1(c, idx, VALUE_BOUND, ch, fs["ventilation_flow_m3_s"],
                                       value_bound="off")["reasons"]
    assert ck._REASON_WORDS["value_is_bound"]                             # a redraw is told why


def test_bound_ok_fields_are_exempt(tmp_path: Path) -> None:
    assert ck.BOUND_OK_FIELDS == ("clearance_fraction", "risk_tolerance")
    c, idx, fs = _bound_paper(tmp_path)
    risk = {"question": "What expected number of transmissions during the lesson was tolerated?",
            "quote": "The tolerated risk was below 0.1 for the lesson", "quantity": "tolerated risk",
            "value": "below 0.1", "symbol": "", "case": "the lesson"}
    ch = _chunk(c, "below 0.1")
    r = _l1(c, idx, risk, ch, fs["risk_tolerance"])
    assert "value_is_bound" not in r["reasons"], r["reasons"]
    # configurable: an empty list exempts nothing; a listed field is exempt
    assert "value_is_bound" in _l1(c, idx, risk, ch, fs["risk_tolerance"], bound_ok_fields=[])["reasons"]
    ch2 = _chunk(c, "stayed above 0.31")
    assert "value_is_bound" not in _l1(c, idx, VALUE_BOUND, ch2, fs["ventilation_flow_m3_s"],
                                       bound_ok_fields=["ventilation_flow_m3_s"])["reasons"]


def test_empty_case_is_a_fields_mode_flag(tmp_path: Path) -> None:
    c, idx, fs = _bound_paper(tmp_path)
    ch = _chunk(c, "lower than 1500")
    r = _l1(c, idx, {**QUOTE_BOUND, "case": " "}, ch, fs["ventilation_flow_m3_s"])
    assert r["verdict"] == "pass" and "empty_case" in r["flags"]           # a flag, never a reject
    assert "empty_case" not in _l1(c, idx, QUOTE_BOUND, ch, fs["ventilation_flow_m3_s"])["flags"]
    assert "empty_case" not in _l1(c, idx, {**QUOTE_BOUND, "case": ""}, ch, None)["flags"]  # open mode: none


# ------------------------------------------------------------------ the export: verifier provenance

def mouth(prompt: str) -> bool:
    return "area of the mouth opening?" in prompt


def _p_chain(tmp: Path, cli: Any, *extra: str) -> tuple[Path, dict[str, Any]]:
    path, cfg = r3d.world_p(tmp)
    gw = r3d.Gateway(r3d.fake_fields)
    assert run(path, "preflight", "--stage", "draft", "--set", "P", *extra) == 0
    assert run(path, "draft", "--set", "P", "--notice-acknowledged", *extra, gw=gw) == 0
    assert run(path, "check", "--set", "P", *extra) == 0
    assert run(path, "preflight", "--stage", "verify", "--set", "P", *extra) == 0
    assert run(path, "verify", "--set", "P", "--notice-acknowledged", *extra, cli=cli) == 0
    assert run(path, "summary", "--set", "P", *extra) == 0
    return path, cfg


def test_a_codex_verified_record_is_distinguishable_in_the_export(tmp_path: Path) -> None:
    clis = r3y.Clis(mouth)
    path, cfg = _p_chain(tmp_path, clis)
    assert clis.codex, "the fixture must send at least one record to codex"
    exp = tmp_path / "export.jsonl"
    assert run(path, "export-partner", "--set", "P", str(exp)) == 0
    rows = jsonl(exp)
    assert all({"verifier", "verifier_refused"} <= set(r) for r in rows)
    cx = [r for r in rows if r["verifier"] == "codex"]
    cl = [r for r in rows if r["verifier"] == "claude"]
    assert cx and cl and len(cx) + len(cl) == len(rows)
    assert all(r["verifier_refused"] == ["claude"] and mouth(r["question"]) for r in cx)
    assert all(r["verifier_refused"] == [] for r in cl)
    # the served model, where the l2 row recorded one
    assert all(r.get("verifier_model") for r in rows)
    assert {r["verifier_model"] for r in cl} == {"claude-opus-5-5"}


def test_candidates_export_has_no_verifier(tmp_path: Path) -> None:
    path, cfg = _p_chain(tmp_path, r2f.OkCLI())
    exp = tmp_path / "cand.jsonl"
    assert run(path, "export-partner", "--set", "P", str(exp), "--candidates") == 0
    rows = jsonl(exp)
    assert rows and all(r["verifier"] is None and r["verifier_refused"] == [] and "verifier_model" not in r
                        for r in rows)


def test_verifier_provenance_of_an_older_accepted_row() -> None:
    # an accepted row written before verifier_model existed: no model key, refusal as a string
    assert dr.verifier_provenance({"verifier": "codex", "verifier_refused": "claude"}) == {
        "verifier": "codex", "verifier_refused": ["claude"]}
    assert dr.verifier_provenance({"verifier": "claude", "verifier_refused": None, "verifier_model": "m"}) == {
        "verifier": "claude", "verifier_refused": [], "verifier_model": "m"}


# ------------------------------------------------------------------ path collisions

@pytest.mark.parametrize("name", ["partner-export-P-pilot.json", "partner-export-P-pilot.json.tmp",
                                  "accepted-P-pilot.jsonl", "partner-verdicts-P.json",
                                  "acceptance-P.jsonl", "verify-state-P-pilot.json"])
def test_export_refuses_a_path_the_run_writes(tmp_path: Path, name: str) -> None:
    path, cfg = _p_chain(tmp_path, r2f.OkCLI(), "--pilot", "6", "--papers", "2")
    out = Path(cfg["out_dir"])
    before = {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}
    with pytest.raises(SystemExit, match="a file this run writes"):
        run(path, "export-partner", "--set", "P", str(out / name), "--pilot", "6", "--papers", "2")
    assert {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()} == before
    # a sibling name in the same directory is fine
    assert run(path, "export-partner", "--set", "P", str(out / "partner-records-P-pilot.jsonl"),
               "--pilot", "6", "--papers", "2") == 0


def test_import_refuses_its_own_output_as_input(tmp_path: Path) -> None:
    path, cfg = _p_chain(tmp_path, r2f.OkCLI(), "--pilot", "6", "--papers", "2")
    out = Path(cfg["out_dir"])
    assert run(path, "export-partner", "--set", "P", str(tmp_path / "x.jsonl"), "--pilot", "6", "--papers", "2") == 0
    rows = jsonl(tmp_path / "x.jsonl")
    own = out / "partner-verdicts-P-pilot.json"         # the owner's file name equals our output's
    own.write_text(json.dumps({"records": [{"id": r["id"], "verdict": "accept", "basis": "sampled"} for r in rows],
                               "sample": {"n": len(rows), "errors": 0, "seed": "s"}}))
    with pytest.raises(SystemExit, match="a file this run writes"):
        run(path, "import-partner-verdicts", "--set", "P", str(own), "--pilot", "6", "--papers", "2")


# ------------------------------------------------------------------ the import, end to end

def test_import_of_a_pilot_shaped_verdict_object(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, cfg = _p_chain(tmp_path, r2f.OkCLI(), "--pilot", "12", "--papers", "3")
    out = Path(cfg["out_dir"])
    exp = tmp_path / "partner-records-P-pilot.jsonl"
    assert run(path, "export-partner", "--set", "P", str(exp), "--pilot", "12", "--papers", "3") == 0
    rows = jsonl(exp)
    assert len(rows) >= 5 and all(r["pilot"] is True for r in rows)
    five = rows[:5]
    # the owner's shape: per-record verdicts (rejects carry a reason, accepts carry none) and a
    # sample summary with a string seed
    verdicts = {"records": [
        {"id": five[0]["id"], "verdict": "reject", "basis": "sampled", "reason": "answer is a different case"},
        {"id": five[1]["id"], "verdict": "accept", "basis": "sampled"},
        {"id": five[2]["id"], "verdict": "reject", "basis": "sampled", "reason": "value is a bound"},
        {"id": five[3]["id"], "verdict": "accept", "basis": "sampled"},
        {"id": five[4]["id"], "verdict": "accept", "basis": "sampled"}],
        "sample": {"n": 5, "errors": 2, "seed": "pilot-seed-2026"}}
    vp = tmp_path / "owner-verdicts.json"
    vp.write_text(json.dumps(verdicts))
    capsys.readouterr()
    assert run(path, "import-partner-verdicts", "--set", "P", str(vp), "--pilot", "12", "--papers", "3") == 0
    res = json.loads((out / "partner-verdicts-P-pilot.json").read_text())
    lo, hi = dr.wilson(2, 5)
    assert lo is not None and hi is not None
    label = f"scripted-clean, judged sample of 5, error rate 40.0% (95% CI {100 * lo:.1f}–{100 * hi:.1f})"
    assert res["status_label"] == label and label in capsys.readouterr().out
    assert res["pilot"] is True and res["error_rate"] == 0.4 and res["sample"] == verdicts["sample"]
    assert res["counts"] == {"exported": len(rows), "verdicts": 5, "scripted_rejects": 0, "sampled": 5,
                             "sampled_rejects": 2}
    assert set(res["rejects"]) == {five[0]["id"], five[2]["id"]}
    p = res["promotion"]
    assert p["n"] == 5 and p["errors"] == 2 and p["upper_bound"] == hi and p["upper_bound_6dp"] == f"{hi:.6f}"
    assert p["met"] is False and p["threshold"] == dr.PROMOTION_THRESHOLD and p["min_judged"] == 30
    assert res["verdicts_sha256"] == g.file_sha(vp)
