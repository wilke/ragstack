"""Judge validation 2: the study-gateway /3 path (KA3, KS3) and its single self-check run, C's graded
majority and the K3 / KY rules, the rater sheet's rubric and examples blocks, and the committed
self-check draft. Offline: a fake study gateway (FakeHTTP) with invented values; no model, no network.

Each test names the mutation it catches.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parents[1] / "scripts" / "eval"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import alias_judge3_selfcheck as sc  # noqa: E402
import alias_judge3_sheet as sheet  # noqa: E402
import glossbench_alias_judge as jd  # noqa: E402
import test_glossbench_alias_judge as tgj  # noqa: E402

FAKE_GW = "http://study-gateway.invalid:9/v1"  # a fake, unresolvable value (the client parses a port)
GRADED = '{"reason": "value stated", "answers": "partial"}'


@pytest.fixture()
def world(tmp_path: Path) -> Path:
    cfg = tgj.make_world(tmp_path)
    assert jd.main(["--config", str(cfg), "build-calib"], prov=tgj.PROV) == 0
    return cfg


def gw3(world: Path, **extra: Any) -> Path:
    return tgj.gw_cfg(world, tag="argon3", model="Fake Argon", base_url=FAKE_GW, judge_version="alias-judge/3",
                      **extra)


# ------------------------------------------------------------------ (a) the study-gateway /3 path

def test_study_gateway_runs_v3_with_the_graded_parser_and_the_notice(world: Path) -> None:
    # mutation: parsing a /3 reply with the binary parser (every grade lost), sending /2's closing line,
    # or dropping the --notice-acknowledged gate fails this
    cfg = gw3(world)
    assert jd.main(["--config", str(cfg), "preflight", "--backend", "gateway"], prov=tgj.PROV) == 0
    with pytest.raises(SystemExit, match="notice-acknowledged"):
        jd.main(["--config", str(cfg), "label", "--backend", "gateway"], prov=tgj.PROV)
    http = tgj.FakeHTTP(lambda b: tgj.chat(GRADED, model="Fake Argon"))
    assert tgj.label(cfg, "gateway", http) == 0
    out = tgj.out_of(world)
    rows = [json.loads(x) for x in (out / "labels-gw-argon3.jsonl").read_text().splitlines()]
    assert rows and all(r["grade"] == "partial" and r["verdict"] is None for r in rows)
    assert all(r["judge_version"] == "alias-judge/3" and r["system_sha256"] == jd.g.sha_text(jd.JUDGE_SYSTEM_V3)
               for r in rows)
    for b in http.bodies:
        assert b["messages"][0]["content"] == jd.JUDGE_SYSTEM_V3 and b["fallback_model"] == ""
        assert b["messages"][1]["content"].endswith(jd.JUDGE_CLOSING_V3)
    plan = json.loads((out / "preflight-gw-argon3.json").read_text())["plan"]
    w = jd.World(jd.load_config(cfg)[1])
    users = [jd.user_message_for("alias-judge/3", w.prompt(p)[0]) for p in w.pairs()]
    assert plan["est_input_tokens"] == jd.gateway_est_input_tokens("Fake Argon", jd.JUDGE_SYSTEM_V3, users,
                                                                   [1] * len(users))
    assert "GATEWAY SIZE-NOTICE" in (out / "LOG").read_text()


def test_study_gateway_binary_reply_to_v3_stores_no_grade(world: Path) -> None:
    # mutation: mapping a yes/no reply onto a grade fails this (fail closed: missing, never wrong)
    cfg = gw3(world)
    assert tgj.label(cfg, "gateway", tgj.FakeHTTP(lambda b: tgj.chat(tgj.YES, model="Fake Argon"))) == 0
    rows = [json.loads(x) for x in (tgj.out_of(world) / "labels-gw-argon3.jsonl").read_text().splitlines()]
    assert all(r["grade"] is None and r["verdict"] is None for r in rows)


def test_qwen_backend_still_refuses_v3() -> None:
    # mutation: dropping the qwen guard lets the binary Qwen backend store /3 replies as invalid verdicts
    with pytest.raises(SystemExit, match="graded"):
        jd.merged({"qwen": {"judge_version": "alias-judge/3"}})
    jd.merged({"judge_gw": {"judge_version": "alias-judge/3"}})


def _cases_file(tmp: Path, labels: bool = True) -> Path:
    cases = [{**c, "expected_label": (c["suggested"] if labels else "")} for c in sc.SELF_CHECK_CASES_V3[:4]]
    # the test sets its own labels (from `suggested` only to have valid values; no expectation is drawn)
    path = tmp / "selfcheck-cases.json"
    path.write_text(json.dumps(cases))
    return path


def test_study_gateway_selfcheck_runs_each_case_once(world: Path, tmp_path: Path) -> None:
    # mutation: running a case more than once, skipping the notice, or not recording the owner agreement
    cfg = gw3(world, selfcheck_file=str(_cases_file(tmp_path)))
    http = tgj.FakeHTTP(lambda b: tgj.chat(GRADED, model="Fake Argon"))
    assert tgj.label(cfg, "gateway-selfcheck", http) == 0
    assert len(http.bodies) == 4
    names = [c["name"] for c in sc.SELF_CHECK_CASES_V3[:4]]
    for b, c in zip(http.bodies, sc.SELF_CHECK_CASES_V3[:4], strict=False):
        assert f"QUESTION: {c['question']}" in b["messages"][1]["content"]
    out = tgj.out_of(world)
    res = json.loads((out / "selfcheck-gw-argon3-selfcheck.json").read_text())
    assert res["cases"] == 4 and res["graded"] == 4 and set(res["per_case"]) == set(names)
    want = sum(1 for c in sc.SELF_CHECK_CASES_V3[:4] if c["suggested"] == "partial")
    assert res["agree_with_owner"] == want
    assert json.loads((out / "preflight-gw-argon3-selfcheck.json").read_text())["plan"]["calls"] == 4


def test_study_gateway_selfcheck_guards(world: Path, tmp_path: Path) -> None:
    # mutation: running the self-check with empty owner labels, or on a binary prompt, fails this
    with pytest.raises(SystemExit, match="owner-set"):
        jd.main(["--config", str(gw3(world)), "preflight", "--backend", "gateway-selfcheck"], prov=tgj.PROV)
    cfg2 = tgj.gw_cfg(world, tag="argon2", model="Fake Argon", base_url=FAKE_GW,
                      selfcheck_file=str(_cases_file(tmp_path)))
    with pytest.raises(SystemExit, match="graded"):
        jd.main(["--config", str(cfg2), "preflight", "--backend", "gateway-selfcheck"], prov=tgj.PROV)


# ------------------------------------------------------------------ (b) graded majority, K3, KY

@pytest.mark.parametrize("grades,want", [
    (["full", "full", "full", "partial", "none"], "full"),
    (["full", "full", "partial", "partial", "none"], None),      # no grade above half
    (["full", "full", None, None, "partial"], "full"),          # 2 of 3 valid
    (["full", None, None, None, "partial"], None),              # 1 of 2 valid is not above half
    ([None] * 5, None),
    (["none", "none", "none", "full", "full"], "none"),
])
def test_graded_majority(grades: list[str | None], want: str | None) -> None:
    # mutation: a plurality instead of "more than half", or counting invalid samples, fails a case
    assert jd.graded_majority(grades) == want


def test_k3_rule() -> None:
    # mutation: E read beyond sample 0, C past c_n, a tiebreak without disagreement, or a label without
    # Argon after a disagreement fails this
    c = ["partial"] * 3 + ["full"] * 2
    assert jd.k3_label(c, ["partial", "full", "full"], "none") == ("partial", "agree")
    assert jd.k3_label(c, ["full", "partial", "partial"], "none") == ("none", "tiebreak")
    assert jd.k3_label(["full", "full", "partial", "partial", "none"], ["full"], "full") == ("full", "tiebreak")
    assert jd.k3_label(c, [None, "partial"], None) == (None, "unjudged")
    assert jd.k3_label(c + ["full"] * 4, ["partial"], None) == ("partial", "agree")  # first 5 samples only
    assert jd.k3_label(c, ["partial"], "yes") == ("partial", "agree")
    assert jd.k3_label(c, ["full"], "yes") == (None, "unjudged")  # a binary value is no grade


def test_ky_rule() -> None:
    # mutation: KY answering without all three yes, or not flagging a missing label, fails this
    assert jd.ky_label([1, 1, 1, 0, 0], [1, 0, 0], True) == (1, False)
    assert jd.ky_label([1, 1, 1, 0, 0], [0], True) == (0, False)
    assert jd.ky_label([1, 1, 1, 0, 0], [1], None) == (0, True)
    assert jd.ky_label([1, 1, None, 0, 0], [1], True) == (0, True)     # C tied: missing, counts as not
    assert jd.ky_label([1, 1, 1, 0, 0], [None, 1], True) == (0, True)  # E sample 0 invalid


# ------------------------------------------------------------------ (c) the rater sheet

def _block_after(text: str, heading: str) -> str:
    i = text.index(f"\n{heading}\n")
    s = text.index("```text\n", i) + len("```text\n")
    return text[s:text.index("\n```", s)]


def test_rater_sheet_rubric_and_examples_equal_the_prompt(tmp_path: Path) -> None:
    # mutation: any edit to the sheet's rubric or examples text (or building them from anything but
    # JUDGE_SYSTEM_V3) fails this
    items = [{"item_id": "i1", "question": "What is the half-life of zorb?", "term": "zorb",
              "passage": "Zorb had a half-life of 3 days.", "title": "T", "defs": [["ZB", "zorb"]]}]
    text = sheet.write_sheet(tmp_path / "sheet-RA2.md", "RA2", items).read_text(encoding="utf-8")
    b = jd.judge_v3_blocks()
    assert _block_after(text, sheet.RUBRIC_HEADING).encode() == b["rubric"].encode()
    assert _block_after(text, sheet.EXAMPLES_HEADING).encode() == b["examples"].encode()
    assert b["rubric"] in jd.JUDGE_SYSTEM_V3 and b["examples"] in jd.JUDGE_SYSTEM_V3
    # the same fields as the judge, without the closing line and without any reference answer
    ctx = _block_after(text, "## Item 1: i1")
    assert ctx == jd.judge_prompt_v3(items[0]["question"], "zorb", items[0]["passage"], "T",
                                     [("ZB", "zorb")]).removesuffix(jd.JUDGE_CLOSING_V3).rstrip("\n")
    assert "answer:" not in text.lower() and all(c in text for c in sheet.UNSURE_CODES)


# ------------------------------------------------------------------ (d) the committed self-check draft

def test_committed_selfcheck_draft_is_unlabelled_and_valid() -> None:
    # mutation: copying `suggested` into expected_label, or dropping a case, fails this
    cases = sc.load_cases()
    assert len(cases) == 23 and len({c["name"] for c in cases}) == 23
    assert all(c["expected_label"] == "" for c in cases)
    assert all(c["suggested"] in sc.GRADES for c in cases)  # metadata only, never read as expected
    for c in cases:
        jd.judge_prompt_v3(c["question"], c["term"], c["passage"], c["title"], c["defs"])
        for a, _p in c["defs"]:
            assert re.search(re.escape(a), c["passage"]), (c["name"], a)
    with pytest.raises(SystemExit, match="owner-set"):
        sc.require_owner_labels(cases)
    # labels set by the owner (here: arbitrary valid values) lift the guard; `suggested` plays no part
    sc.require_owner_labels([{**c, "expected_label": "none", "suggested": ""} for c in cases])
