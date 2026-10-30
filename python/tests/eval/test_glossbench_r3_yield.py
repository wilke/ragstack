"""Round 3 after the set-P pilot: field assignment by content, the pre-drafting yield
estimate, the drafter's yield watch and the codex fallback verifier.

* fields mode pairs a chunk with a field only when the chunk names one of the field's
  terms (the wording proxy's normalisation and stemming) AND prints a number of a
  compatible dimension; round 2's prefilter (a term OR a unit) is recorded beside it;
* every round-3 pool records ``yield`` (expected_yield, gate, status) and ``draft``
  refuses a YIELD-NOT-ADEQUATE pool without ``--allow-low-yield``;
* ``draft`` pauses (75) after the first ``yield_watch.after_jobs`` jobs when the
  layer-1 pass rate is below ``yield_watch.min_pass``; a resume needs
  ``--accept-low-yield``;
* a Claude usage-policy refusal is verified by codex (one at a time, its own isolation
  check), recorded per record; a codex failure is ``verifier_unavailable``.

Fake gateway, fake CLIs; synthetic corpora; no store, no network, no model.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.eval import test_glossbench_r2_fields as r2f
from tests.eval import test_glossbench_r3_draft as r3d

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

import _glossbench_r2 as g  # noqa: E402
import glossbench_r2_draft as dr  # noqa: E402
import glossbench_r2_fields as fl  # noqa: E402
import glossbench_r3_pool as pool  # noqa: E402

PROV = r3d.PROV
run, jsonl = r3d.run, r3d.jsonl

FLOW = fl.FieldSpec("ventilation_flow_m3_s", "m3/s", "outdoor-air supply flow", ("ventilation rate", "outdoor air"))
PEOPLE = fl.FieldSpec("occupant_count", "person", "people in the room", ("occupants", "students"))
SHARE = fl.FieldSpec("mask_share", "fraction", "share wearing masks", ("masks",))
ODD = fl.FieldSpec("odd_unit", "furlong/fortnight", "an unmappable unit", ("ventilation rate",))


# ------------------------------------------------------------------ 1a. eligibility: the number gates

@pytest.mark.parametrize("text,spec,want,round2", [
    ("The ventilation rate was 0.5 m3/s.", FLOW, True, True),             # a term and a compatible unit
    ("Qb = 0.5 m3/h in the ward.", FLOW, True, True),                     # symbol-only: no term, still eligible
    ("A pump moved 0.5 m3/s of water.", FLOW, True, True),                # no term: eligible (terms only rank)
    ("The ventilation rate gave 2 air changes per hour.", FLOW, True, True),  # ACH vs m3/s: related
    ("λa = 0.65 h−1 in the room.", FLOW, True, True),                     # h^-1: related to a volume flow
    ("The ventilation rate was 12 m in height.", FLOW, False, True),       # a term, an incompatible unit
    ("The ventilation rate was raised.", FLOW, False, False),              # no number at all
    ("Twenty-one students shared the room.", PEOPLE, True, True),           # count: a number word, no unit
    ("I = 1 in every run.", PEOPLE, True, False),                          # count: a bare number, no term
    ("In 5 PFU samples it grew.", PEOPLE, True, False),                    # count: a unit outside the map
    ("The students sat 2 m apart.", PEOPLE, False, True),                  # count: a length is incompatible
    ("About 40 % wore them.", SHARE, True, False),                         # fraction: a dimensionless unit
    ("The ventilation rate was 3 furlong/fortnight.", ODD, False, True),   # the field's unit is unmappable
])
def test_eligibility_is_a_compatible_printed_number(text: str, spec: fl.FieldSpec, want: bool,
                                                     round2: bool) -> None:
    assert fl.field_eligible(text, spec) is want
    assert (spec.field in fl.eligible_fields(text, [spec])) is want
    assert fl.prefilter(text, spec) is round2                            # round 2's rule is unchanged


def test_term_matching_is_the_wording_proxys() -> None:
    import wording_proxy as wp

    spec = fl.FieldSpec("f", "m", "d", ("Air-Change Rates", "Qv"))
    assert fl.terms_present("the air change rate was 2 m", spec) == ["Air-Change Rates"]
    assert fl.terms_present("separated air", spec) == []                   # not a substring test
    assert fl.terms_present("Qv = 2 m", spec) == ["Qv"]                     # a two-letter token is a word
    assert fl.terms_present("Q = 2 m", fl.FieldSpec("f", "m", "d", ("Q",))) == []   # a 1-char term never matches
    assert wp.words("Air-Change Rates") == frozenset({"air", "change", "rate"})
    assert fl.term_rank("the air change rate was 2 m", spec) == 0 and fl.term_rank("2 m", spec) == 1


@pytest.mark.parametrize("text,spec,want", [
    ("The ventilation rate was 0.5 m3/s.", FLOW, "bound"),                 # a term just before the number
    ("Qb = 0.5 m3/h in the ward.", FLOW, "bound"),                         # a value statement with its unit
    ("A pump moved 0.5 m3/s of water.", FLOW, "eligible"),                 # compatible, bound to nothing
    ("Ventilation rate fell. It was 0.5 m3/s.", FLOW, "eligible"),         # the term is in another sentence
    ("Ventilation rate fell, to 0.5 m3/s.", FLOW, "bound"),                # ... and here in the same one
    ("The ventilation rate in the large hall next to the old gym was 0.5 m3/s.", FLOW, "eligible"),  # > window
    ("Qb = 0.5 in the ward.", FLOW, "ineligible"),                          # no compatible number
    ("I = 1 in every run.", PEOPLE, "bound"),                              # count: a value statement, no unit
    ("Twenty-one students shared the room.", PEOPLE, "eligible"),          # the term follows the number
])
def test_job_class(text: str, spec: fl.FieldSpec, want: str) -> None:
    assert fl.job_class(text, spec) == want


def test_plan_ranks_term_chunks_first_and_round2_order_is_kept() -> None:
    spec = fl.FieldSpec("f", "m", "d", ("t",))
    fc = {**fl.FIELD_DEFAULTS, "path": "x"}
    rows = [{"chunk_id": f"c{i}", "paper": "p", "stratum": "V", "field_candidates": ["f"]} for i in range(8)]
    targets = {("p", "V"): 2}
    base, jobs0 = fl.plan_strata([spec], fc, rows, targets, "seed")
    plain = sorted(jobs0)
    term = sorted({f"c{i}" for i in range(8)} - set(plain))[:2]          # two chunks the seed did not pick
    rank = {(r["chunk_id"], "f"): (0 if r["chunk_id"] in term else 1) for r in rows}
    _, jobs = fl.plan_strata([spec], fc, rows, targets, "seed", rank=rank)
    assert sorted(jobs) == term
    _, again = fl.plan_strata([spec], fc, rows, targets, "seed", rank=None)
    assert again == jobs0                                                 # no rank: round 2's draw


# ------------------------------------------------------------------ 1 + 2. the P pool

def test_fields_pool_gates_on_the_number_and_ranks_by_term(tmp_path: Path) -> None:
    _, cfg = r3d.world_p(tmp_path)
    header, rows = r2f.pool_rows(cfg)
    specs = {d["field"]: fl.FieldSpec.from_dict(d) for d in header["field_specs"]}
    chunks = {c.chunk_id: c.content for c in g.read_chunks(cfg["corpora"]["P"]["chunks"])}
    jobs = [(r, f) for r in rows for f in r["field_jobs"]]
    assert jobs and all(fl.field_eligible(chunks[r["chunk_id"]], specs[f]) for r, f in jobs)
    assert all(set(r["field_jobs"]) <= set(r["field_candidates"]) for r in rows)
    assert all(set(r["field_terms"]) <= set(r["field_candidates"]) for r in rows)
    assert all((f in r["field_terms"]) == bool(fl.terms_present(chunks[r["chunk_id"]], specs[f]))
               for r in rows for f in r["field_candidates"])
    # "Qv gave 1.5 air changes per hour" names no flow term: eligible all the same (symbol-only kept)
    ach = [r for r in rows if "air changes per hour" in chunks[r["chunk_id"]]]
    assert ach and all("ventilation_flow_m3_s" in r["field_candidates"] and
                       "ventilation_flow_m3_s" not in r["field_terms"] for r in ach)
    # ranking: within a (paper, stratum, field) cell no term-less chunk is drafted while a term chunk waits
    cells: dict[tuple[str, str, str], list[tuple[bool, bool]]] = {}
    for r in rows:
        for f in r["field_candidates"]:
            cells.setdefault((r["paper"], r["stratum"], f), []).append((f in r["field_terms"], f in r["field_jobs"]))
    for v in cells.values():
        assert not (any(t and not j for t, j in v) and any(j and not t for t, j in v))
    m = json.loads((Path(cfg["out_dir"]) / "pool-manifest-P.json").read_text())
    y = m["yield"]
    a = y["assignment"]
    assert a["eligible"]["pairs"] == sum(len(r["field_candidates"]) for r in rows)
    assert a["eligible_with_term"]["pairs"] == sum(len(r["field_terms"]) for r in rows)
    terms = sum(f in r["field_terms"] for r, f in jobs)
    assert y["term_in_chunk"] == {"true": terms, "false": len(jobs) - terms, "share_true": terms / len(jobs)}
    assert 0 < terms < len(jobs)                                          # symbol-only jobs exist and are counted
    assert sum(y["classes"].values()) == y["jobs"] == len(jobs) and y["calibrated"] is False
    assert y["expected_yield"] == pytest.approx(y["classes"]["bound"] / y["target_jobs"])
    assert header["yield"] == y and y["target_jobs"] == m["fields"]["plan"]["total_target"]


def _calibration(path: Path, rates: dict[str, Any]) -> Path:
    path.write_text(json.dumps({"schema": pool.CALIBRATION_SCHEMA, "rates": rates, "jobs": 100}))
    return path


def test_a_calibration_turns_classes_into_a_predicted_yield(tmp_path: Path) -> None:
    _, cfg = r3d.world_p(tmp_path)
    cal = _calibration(tmp_path / "cal.json", {"bound": 0.2, "eligible": 0.1, "ineligible": 0.0})
    cfg["pool_r3"]["yield_calibration"] = str(cal)
    m = pool.build(cfg, "P", g.out_dir(cfg), provenance=lambda: PROV)
    y = m["yield"]
    c = y["classes"]
    assert y["calibrated"] is True and y["calibration"]["sha256"] == g.file_sha(cal)
    assert y["expected_yield"] == pytest.approx((0.2 * c["bound"] + 0.1 * c["eligible"]) / y["target_jobs"])
    assert y["status"] == pool.YIELD_LOW                                  # below the 0.5 gate
    for bad in ({"bound": 0.5}, {"bound": 2, "eligible": 0, "ineligible": 0}):
        cfg["pool_r3"]["yield_calibration"] = str(_calibration(tmp_path / "bad.json", bad))
        with pytest.raises(SystemExit, match="yield_calibration"):
            pool.build(cfg, "P", g.out_dir(cfg), dry=True, provenance=lambda: PROV)


def test_backtest_replays_the_estimator_on_drafted_jobs(tmp_path: Path) -> None:
    path, cfg = r3d.world_p(tmp_path)
    out = Path(cfg["out_dir"])
    assert run(path, "preflight", "--stage", "draft", "--set", "P") == 0
    assert run(path, "draft", "--set", "P", "--notice-acknowledged", gw=r3d.Gateway(r3d.fake_fields)) == 0
    drafts = jsonl(out / "draft-P.jsonl")
    assert all(isinstance(d["term_in_chunk"], bool) for d in drafts)     # recorded per job
    res_p, nc = tmp_path / "bt.json", tmp_path / "nc.jsonl"
    assert pool.main(["--private-config", str(path), "backtest", "--set", "P", "--drafts", str(out / "draft-P.jsonl"),
                      "--out", str(res_p), "--negative-controls", str(nc)]) == 0
    res = json.loads(res_p.read_text())
    jobs = {(d["chunk_id"], d["field"]) for d in drafts}
    passed = {(d["chunk_id"], d["field"]) for d in drafts if d["l1_inline"]["verdict"] == "pass"}
    assert res["jobs"] == len(jobs) and res["observed"]["passes"] == len(passed)
    assert sum(v["jobs"] for v in res["by_class"].values()) == len(jobs)
    assert res["predicted"]["calibrated_in_sample"] == pytest.approx(len(passed) / len(jobs))
    assert 0 <= res["predicted"]["calibrated_leave_one_paper_out"] <= 1
    nones = {(d["chunk_id"], d["field"]) for d in drafts if d["attempt"] == 1
             and "no_quantity" in d["l1_inline"]["reasons"]}
    rows = jsonl(nc)
    assert {(r["chunk_id"], r["field"]) for r in rows} == nones and res["first_attempt_none"] == len(nones)
    assert all(set(r) == {"chunk_id", "field", "stratum", "label"} and r["label"] == "no_quantity" for r in rows)
    # the backtest output is a valid calibration
    cfg["pool_r3"]["yield_calibration"] = str(res_p)
    assert pool.build(cfg, "P", out, dry=True, provenance=lambda: PROV)["yield"]["calibrated"] is True


def test_open_pool_yield_counts_the_plans_fill(tmp_path: Path) -> None:
    # world O: 3 eval papers, each with 3 V and 7 M_num population chunks
    m, _, rows = r3d.r3p.build(tmp_path / "a")
    y = m["yield"]
    assert (y["jobs"], y["jobs_passing"], y["target_jobs"], y["expected_yield"]) == (30, 30, 30, 1.0)
    assert y["status"] == pool.YIELD_OK and y["mode"] == "open" and y["calibrated"] is None
    assert y["by_stratum"]["V"]["jobs"] == 9 and y["by_stratum"]["M_num"]["jobs"] == 21
    # asking 13 V per paper: 30 of 3 x (13 + 7) = 60
    m2, _, _ = r3d.r3p.build(tmp_path / "b", pool_r3={"v_per_paper": 13})
    assert m2["yield"]["expected_yield"] == pytest.approx(30 / 60) and m2["yield"]["status"] == pool.YIELD_OK
    m3, _, _ = r3d.r3p.build(tmp_path / "c", pool_r3={"v_per_paper": 13, "yield_gate": 0.6})
    assert m3["yield"]["status"] == pool.YIELD_LOW and m3["design"]["yield_gate"] == 0.6


def test_open_yield_rechecks_the_chunk_text(tmp_path: Path) -> None:
    corpus = g.build_corpus([g.parse_chunk({"id": "c1", "doc_id": "d", "content": "no value here", "start_char": 0,
                                            "end_char": 13}, "t")], "doc_id")
    row = {"chunk_id": "c1", "stratum": "V"}
    y = pool.expected_yield([(row, None)], corpus, {}, [], 2, 0.5, False)
    assert (y["jobs"], y["jobs_passing"], y["expected_yield"], y["status"]) == (1, 0, 0.0, pool.YIELD_LOW)
    y = pool.expected_yield([], corpus, {}, [], 0, 0.5, False)
    assert y["expected_yield"] == 0.0 and y["status"] == pool.YIELD_LOW


@pytest.mark.parametrize("bad", [1.5, -0.1, "0.5", True])
def test_yield_gate_is_validated(bad: Any) -> None:
    with pytest.raises(SystemExit, match="yield_gate"):
        pool.pool_config({"pool_r3": {"yield_gate": bad}}, "O")


def _low_world_p(tmp: Path) -> tuple[Path, dict[str, Any]]:
    path, cfg = r3d.world_p(tmp)
    cfg["pool_r3"]["yield_calibration"] = str(_calibration(tmp / "cal.json", {"bound": 0.07, "eligible": 0.07,
                                                                          "ineligible": 0.07}))  # the pilot's rate
    path.write_text(json.dumps(cfg))
    pool.build(cfg, "P", g.out_dir(cfg), provenance=lambda: PROV)
    return path, cfg


def test_draft_refuses_a_low_yield_pool_without_the_flag(tmp_path: Path) -> None:
    path, cfg = _low_world_p(tmp_path)
    out = Path(cfg["out_dir"])
    h, _ = r2f.pool_rows(cfg)
    assert h["yield"]["status"] == pool.YIELD_LOW
    assert run(path, "preflight", "--stage", "draft", "--set", "P") == 0
    pf = json.loads((out / "preflight-draft-P.json").read_text())
    assert pf["plan"]["pool_yield"]["status"] == pool.YIELD_LOW
    gw = r3d.Gateway(r3d.fake_fields)
    assert run(path, "draft", "--set", "P", "--notice-acknowledged", gw=gw) == 1
    assert not gw.bodies and not (out / "draft-state-P.json").exists()
    assert run(path, "draft", "--set", "P", "--notice-acknowledged", "--allow-low-yield", gw=gw) == 0
    st = json.loads((out / "draft-state-P.json").read_text())
    assert st["low_yield_allowed"]["status"] == pool.YIELD_LOW and gw.bodies


def test_draft_refuses_a_round3_pool_without_an_estimate(tmp_path: Path) -> None:
    path, cfg, _ = r3d.world_o(tmp_path)
    p = Path(cfg["out_dir"]) / "pool-O.jsonl"
    lines = p.read_text().splitlines()
    h = json.loads(lines[0])
    del h["yield"]
    p.write_text("\n".join([json.dumps(h, sort_keys=True), *lines[1:]]) + "\n")
    assert run(path, "preflight", "--stage", "draft", "--set", "O") == 0
    gw = r3d.Gateway(r3d.fake_open)
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=gw) == 1 and not gw.bodies


# ------------------------------------------------------------------ 3. the yield watch

def _watch_world(tmp: Path, after: int, min_pass: float) -> tuple[Path, dict[str, Any]]:
    path, cfg, _ = r3d.world_o(tmp)
    cfg["yield_watch"] = {"after_jobs": after, "min_pass": min_pass}
    cfg["drafter"]["inflight"] = 1                                       # a deterministic count at the pause
    path.write_text(json.dumps(cfg))
    return path, cfg


def none_answer(messages: list[dict[str, str]]) -> dict[str, Any]:
    return {"none": True}


def test_yield_watch_pauses_and_resumes_only_when_accepted(tmp_path: Path) -> None:
    path, cfg = _watch_world(tmp_path, 5, 0.3)
    out = Path(cfg["out_dir"])
    assert run(path, "preflight", "--stage", "draft", "--set", "O") == 0
    gw = r3d.Gateway(none_answer)
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=gw) == 75
    st = json.loads((out / "draft-state-O.json").read_text())
    assert st["status"] == "paused" and st["reason"].startswith("low_yield: 0 of 5 jobs passed layer 1")
    assert "25 jobs left" in st["reason"] and "--accept-low-yield" in st["reason"]
    assert st["yield_watch"]["status"] == "below" and (st["yield_watch"]["jobs"], st["yield_watch"]["passes"]) == (5, 0)
    assert len(gw.bodies) == 5                                           # no job after the watch point
    # resume without the acknowledgement: refused, no call
    assert run(path, "preflight", "--stage", "draft", "--set", "O") == 0
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", "--resume", gw=gw) == 75
    assert len(gw.bodies) == 5
    # with it: the run completes and the acceptance is recorded
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", "--resume", "--accept-low-yield", gw=gw) == 0
    st = json.loads((out / "draft-state-O.json").read_text())
    assert st["status"] == "complete" and st["yield_watch"]["status"] == "below_accepted"
    assert st["yield_watch"]["accepted"] and len(gw.bodies) == 30


def test_yield_watch_passes_a_good_run(tmp_path: Path) -> None:
    path, cfg = _watch_world(tmp_path, 5, 0.3)
    assert run(path, "preflight", "--stage", "draft", "--set", "O") == 0
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=r3d.Gateway(r3d.fake_open)) == 0
    w = json.loads((Path(cfg["out_dir"]) / "draft-state-O.json").read_text())["yield_watch"]
    assert w["status"] == "passed" and w["jobs"] == 5 and w["rate"] >= 0.3


def test_yield_watch_at_the_last_job_completes(tmp_path: Path) -> None:
    path, cfg = _watch_world(tmp_path, 30, 0.3)                          # world O has 30 jobs
    assert run(path, "preflight", "--stage", "draft", "--set", "O") == 0
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=r3d.Gateway(none_answer)) == 0
    w = json.loads((Path(cfg["out_dir"]) / "draft-state-O.json").read_text())["yield_watch"]
    assert w["status"] == "below_run_complete" and (w["jobs"], w["passes"]) == (30, 0)


def test_yield_watch_not_reached_is_recorded(tmp_path: Path) -> None:
    path, cfg = _watch_world(tmp_path, 100, 0.3)
    assert run(path, "preflight", "--stage", "draft", "--set", "O") == 0
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=r3d.Gateway(none_answer)) == 0
    w = json.loads((Path(cfg["out_dir"]) / "draft-state-O.json").read_text())["yield_watch"]
    assert w["status"] == "not_reached" and w["jobs"] == 30


def test_yield_watch_is_round3_only(tmp_path: Path) -> None:
    # round 2's pool (no yield block, no watch): a run of nothing but "none" completes
    from tests.eval import test_glossbench_r2_draft as r2d

    path, cfg = r2d.setup(tmp_path)
    c = json.loads(path.read_text())
    c["yield_watch"] = {"after_jobs": 1, "min_pass": 0.99}
    path.write_text(json.dumps(c))
    assert r2d.run(path, "preflight", "--stage", "draft", "--set", "O") == 0
    gw = r3d.Gateway(none_answer)
    assert dr.main(["--private-config", str(path), "draft", "--set", "O", "--notice-acknowledged"],
                   provenance=lambda: r2d.PROV, http_factory=lambda: gw) == 0
    st = json.loads((Path(cfg["out_dir"]) / "draft-state-O.json").read_text())
    assert "yield_watch" not in st and "low_yield_allowed" not in st


# ------------------------------------------------------------------ 4. the codex fallback

AUP = ("API Error: Claude's safeguards flagged this message (https://www.anthropic.com/legal/aup). "
       "Please rephrase your request.")
OK_VERDICT = {"supports": True, "single_reading": True, "same_case": True, "symbol_unique": True, "reason": "ok"}


class Clis:
    """A fake ``subprocess.run`` for both CLIs: Claude refuses the prompts ``refuse`` matches;
    codex answers ``codex_verdict`` (or fails with ``codex_rc``)."""

    def __init__(self, refuse: Any, codex_ctx: int = 2600, codex_rc: int = 0, codex_verdict: Any = OK_VERDICT,
                 refusal_shape: str = "json") -> None:
        self.refuse, self.codex_ctx, self.codex_rc, self.codex_verdict = refuse, codex_ctx, codex_rc, codex_verdict
        self.refusal_shape = refusal_shape
        self.claude: list[dict[str, Any]] = []
        self.codex: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.active = self.max_active = 0

    def __call__(self, cmd: list[str], *, input: str, capture_output: bool, text: bool, cwd: str,  # noqa: A002
                 timeout: int) -> SimpleNamespace:
        if cmd[0] == "codex":
            return self._codex(cmd, input, cwd)
        self.claude.append({"input": input, "cwd": cwd})
        if self.refuse(input):
            if self.refusal_shape == "stderr":
                return SimpleNamespace(returncode=1, stdout="", stderr=AUP)
            return SimpleNamespace(returncode=1, stdout=json.dumps({"type": "result", "is_error": True,
                                                                     "result": AUP}), stderr="")
        out = {"result": json.dumps(OK_VERDICT), "is_error": False, "total_cost_usd": 0.01,
               "modelUsage": {"claude-opus-5-5": {"inputTokens": 400, "outputTokens": 30}}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(out), stderr="")

    def _codex(self, cmd: list[str], prompt: str, cwd: str) -> SimpleNamespace:
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.codex.append({"cmd": cmd, "input": prompt, "cwd": cwd, "cwd_files": list(Path(cwd).iterdir()),
                               "system": Path(next(c.split("=", 1)[1].strip('"') for c in cmd
                                                   if c.startswith("model_instructions_file="))).read_text()})
        try:
            time.sleep(0.02)
            if self.codex_rc:
                return SimpleNamespace(returncode=self.codex_rc, stdout="", stderr="codex: boom")
            Path(cmd[cmd.index("-o") + 1]).write_text(json.dumps(self.codex_verdict))
            ev = [{"type": "thread.started", "thread_id": "t"}, {"type": "turn.started"},
                  {"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}},
                  {"type": "turn.completed", "usage": {"input_tokens": self.codex_ctx, "output_tokens": 40}}]
            return SimpleNamespace(returncode=0, stdout="\n".join(json.dumps(e) for e in ev), stderr="")
        finally:
            with self.lock:
                self.active -= 1


def _drafted(tmp: Path, **verifier: Any) -> tuple[Path, dict[str, Any]]:
    path, cfg, _ = r3d.world_o(tmp)
    cfg["verifier"].update(verifier)
    path.write_text(json.dumps(cfg))
    assert run(path, "preflight", "--stage", "draft", "--set", "O") == 0
    assert run(path, "draft", "--set", "O", "--notice-acknowledged", gw=r3d.Gateway(r3d.fake_open)) == 0
    assert run(path, "check", "--set", "O") == 0
    assert run(path, "preflight", "--stage", "verify", "--set", "O") == 0
    return path, cfg


def dusk(prompt: str) -> bool:
    return "at dusk" in prompt


def l2_rows(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in jsonl(Path(cfg["out_dir"]) / "acceptance-O.jsonl") if r["layer"] == "l2"]


@pytest.mark.parametrize("shape", ["json", "stderr"])
def test_a_refused_record_is_verified_by_codex(tmp_path: Path, shape: str) -> None:
    path, cfg = _drafted(tmp_path)
    clis = Clis(dusk, refusal_shape=shape)
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=clis) == 0
    l2 = l2_rows(cfg)
    by = {r["verifier"] for r in l2}
    assert by == {"claude", "codex"}
    cx = [r for r in l2 if r["verifier"] == "codex"]
    assert len(cx) == len(clis.codex) == 3 and all(r["verdict"] == "pass" and r["verifier_refused"] == "claude"
                                                   for r in cx)
    assert all(r["verifier_refused"] is None for r in l2 if r["verifier"] == "claude")
    vrows = jsonl(Path(cfg["out_dir"]) / "verify-O.jsonl")
    assert sum(r["verifier"] == "codex" for r in vrows) == 3
    assert all("safeguards" in r["refusal_text"] for r in vrows if r["verifier"] == "codex")
    # not a transport error, not a usage limit: no failure counted, no retry on Claude
    st = json.loads((Path(cfg["out_dir"]) / "verify-state-O.json").read_text())
    tot = st["totals"]["since_start"]
    assert not any(k.startswith("gateway_failures") for k in tot) and tot["cli_refusals"] == 3
    assert tot["codex_calls"] == 3 and sum(dusk(c["input"]) for c in clis.claude) == 3
    # the same prompt and system prompt, the same empty cwd, the proven flags, one at a time
    vcwd = str(tmp_path / "vcwd")
    for c in clis.codex:
        cmd = c["cmd"]
        assert c["cwd"] == vcwd and cmd[cmd.index("-C") + 1] == vcwd and not c["cwd_files"]
        assert c["system"] == dr.ck.VERIFY_SYSTEM and dusk(c["input"])
        for flag in ("exec", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check"):
            assert flag in cmd
        assert cmd[cmd.index("-s") + 1] == "read-only" and cmd[cmd.index("-m") + 1] == "gpt-6-astra"
        assert cmd[-1] == "-" and "--json" in cmd
    assert clis.max_active == 1
    assert not any((tmp_path / "vcwd").iterdir())
    assert run(path, "summary", "--set", "O") == 0
    s = json.loads((Path(cfg["out_dir"]) / "acceptance-summary-O.json").read_text())
    assert s["layer2_by_verifier"]["codex"] == {"pass": 3} and s["excluded"] == {"verifier_unavailable": 0}
    assert s["verifier_refused"] == {"claude": 3} and not any("refus" in k for k in s["rejects_by_code"])
    acc = jsonl(Path(cfg["out_dir"]) / "accepted-O.jsonl")
    assert sum("dusk" in a["question"] for a in acc) == 3
    assert {a["verifier"] for a in acc} == {"claude", "codex"}            # recorded on every record
    assert all((a["verifier_refused"] == "claude") == (a["verifier"] == "codex") for a in acc)


def test_codex_runs_one_at_a_time_under_claude_concurrency(tmp_path: Path) -> None:
    path, cfg = _drafted(tmp_path)
    clis = Clis(lambda p: "fixed for every run" not in p)                 # most records refused
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=clis) == 0
    assert len(clis.codex) >= 20 and clis.max_active == 1


def test_a_refused_first_record_does_not_fail_the_isolation_probe(tmp_path: Path) -> None:
    path, cfg = _drafted(tmp_path)
    clis = Clis(lambda p: True)                                           # every record refused
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=clis) == 0
    l2 = l2_rows(cfg)
    assert len(l2) == 30 and all(r["verifier"] == "codex" and r["verdict"] == "pass" for r in l2)


def test_codex_failure_is_verifier_unavailable_not_accepted(tmp_path: Path) -> None:
    path, cfg = _drafted(tmp_path)
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=Clis(dusk, codex_rc=1)) == 0
    cx = [r for r in l2_rows(cfg) if r["verifier"] == "codex"]
    assert len(cx) == 3 and all(r["verdict"] == "unavailable" and r["reasons"] == ["verifier_unavailable"]
                                for r in cx)
    assert run(path, "summary", "--set", "O") == 0
    s = json.loads((Path(cfg["out_dir"]) / "acceptance-summary-O.json").read_text())
    assert s["excluded"] == {"verifier_unavailable": 3}                  # excluded with that reason ...
    assert "verifier_unavailable" not in s["rejects_by_code"]             # ... and not rejected
    acc = jsonl(Path(cfg["out_dir"]) / "accepted-O.jsonl")
    assert len(acc) == 27 and not any("dusk" in a["question"] for a in acc)


def test_codex_unparseable_is_verifier_unavailable(tmp_path: Path) -> None:
    path, cfg = _drafted(tmp_path)
    clis = Clis(dusk, codex_verdict={"supports": "yes"})
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=clis) == 0
    assert all(r["verdict"] == "unavailable" for r in l2_rows(cfg) if r["verifier"] == "codex")


def test_codex_isolation_failure_is_a_hard_stop(tmp_path: Path) -> None:
    path, cfg = _drafted(tmp_path)
    clis = Clis(dusk, codex_ctx=10_000_000)
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=clis) == 1
    st = json.loads((Path(cfg["out_dir"]) / "verify-state-O.json").read_text())
    assert st["reason"] == "codex_isolation_failed" and len(clis.codex) == 1
    assert not any(r["verifier"] == "codex" for r in l2_rows(cfg))


def test_without_the_fallback_a_refusal_is_left_unrecorded(tmp_path: Path) -> None:
    path, cfg = _drafted(tmp_path, codex_fallback=False)
    clis = Clis(dusk)
    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=clis) == 75
    assert not clis.codex and len(l2_rows(cfg)) == 27
    st = json.loads((Path(cfg["out_dir"]) / "verify-state-O.json").read_text())
    assert st["reason"] == "calls_failed:3"


@pytest.mark.parametrize("stdout,stderr,obj,want", [
    ("", "", {"is_error": True, "result": AUP}, True),
    ("", AUP, None, True),
    (AUP, "", None, True),
    ("", "", {"is_error": True, "result": "x", "stop_reason": "refusal"}, True),
    ("", "", {"is_error": True, "result": "This request violates our Usage Policy."}, True),
    ("", "Claude AI usage limit reached", None, False),                   # a limit, not a refusal
    ("", "", {"is_error": True, "result": "rate limit", "api_error_status": 429}, False),
    ("", "connection reset by peer", None, False),                        # a transport error
    ("", "", {"is_error": True, "result": "overloaded_error"}, False),
])
def test_is_refusal(stdout: str, stderr: str, obj: dict[str, Any] | None, want: bool) -> None:
    assert dr.is_refusal(stdout, stderr, obj) is want


def test_a_usage_limit_still_pauses(tmp_path: Path) -> None:
    path, cfg = _drafted(tmp_path)

    def limited(cmd: list[str], **kw: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr="Claude AI usage limit reached")

    assert run(path, "verify", "--set", "O", "--notice-acknowledged", cli=limited) == 75
    st = json.loads((Path(cfg["out_dir"]) / "verify-state-O.json").read_text())
    assert st["reason"] == "usage_limit"


def test_verify_preflight_names_the_fallback(tmp_path: Path) -> None:
    _, cfg = _drafted(tmp_path)
    pf = json.loads((Path(cfg["out_dir"]) / "preflight-verify-O.json").read_text())
    assert "codex gpt-6-astra, 1 in flight" in pf["plan"]["refusal_fallback"]
