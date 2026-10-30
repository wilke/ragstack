"""Glossary benchmark round 3, the runner side: the ``+attach=`` stamp slot, the
arm file's rule, the widened boost probe, and the contract with the glossary
tools (stamps produced by ``rederive --attach`` itself, not copied literals).
Round 2's grammar must answer exactly as before for every round-2 arm config.

Hermetic: synthetic stamps and files, the fake API of ``test_found10``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import found10 as f10  # noqa: E402
import glossary_boost_probe as gbp  # noqa: E402
from test_found10 import RANKS, FakeAPI, cfg, run, swap  # noqa: E402

QWEN = "llm/2:ModelX:abc:w80000x4:t4096:k0:r0"
VALUE = f"{QWEN}+merge=llm-only@file/1"                         # round 2's pure file, loaded as is
MENTION = f"{QWEN}+merge=llm-only+attach=mention@file/1"        # rederive --attach mention
VALUE_MARKED = f"{QWEN}+merge=llm-only+attach=value@file/1"     # rederive --attach value
BARE_MENTION = "none+merge=llm-only+attach=mention@file/1"
RULES_MENTION = "rules/3+attach=mention@file/1"
SHUF = "+shuf/1:261103|shuf:0123456789ab"

PURE = {"stamp_prefixes": ["llm/2:ModelX:"], "stamps": ["none"], "merge": "llm-only", "shuffle_seed": None}
ARMS_R3 = {
    "value": {**PURE, "attach": None},
    "mention": {**PURE, "attach": "mention"},
    "mention-shuf": {**PURE, "attach": "mention", "shuffle_seed": "261103|shuf"},
    "none": {"stamps": ["none"], "merge": "none", "attach": None, "shuffle_seed": None},
    "rules-value": {"stamps": ["rules/3"], "merge": None, "attach": None, "shuffle_seed": None},
    "rules-mention": {"stamps": ["rules/3"], "merge": None, "attach": "mention", "shuffle_seed": None},
}


# --------------------------------------------------------------------------- grammar
def test_round2_split_stamp_is_unchanged_and_reads_no_attach_slot() -> None:
    assert f10.split_stamp(VALUE) == (QWEN, "llm-only", None)
    assert f10.split_stamp("rules/3@file/1") == ("rules/3", None, None)
    for s in (MENTION, VALUE_MARKED, BARE_MENTION):
        assert f10.split_stamp(s) is None, s
    # without a merge marker round 2's parser reads the slot as part of the base (unchanged);
    # stamp_allowed refuses it for every round-2 arm config (below)
    assert f10.split_stamp(RULES_MENTION) == ("rules/3+attach=mention", None, None)


def test_split_stamp_attach() -> None:
    assert f10.split_stamp_attach(MENTION) == (QWEN, "llm-only", "mention", None)
    assert f10.split_stamp_attach(VALUE) == (QWEN, "llm-only", None, None)
    assert f10.split_stamp_attach(RULES_MENTION) == ("rules/3", None, "mention", None)
    assert f10.split_stamp_attach(MENTION + SHUF) == (QWEN, "llm-only", "mention", ("261103|shuf", "0123456789ab"))
    for bad in (f"{QWEN}+attach=mention+merge=llm-only@file/1",      # the order is merge, then attach
                f"{QWEN}+merge=llm-only+attach=doc@file/1",          # not a rule
                "+attach=mention@file/1", f"{QWEN}+attach=mention", MENTION + "+shuf/1:x"):
        assert f10.split_stamp_attach(bad) is None, bad


@pytest.mark.parametrize("stamp,arm,ok", [
    (VALUE, "value", True),
    (MENTION, "value", False),             # a mention file passed off as the value arm
    (VALUE_MARKED, "value", False),        # value is declared as the unmarked round-2 file
    (MENTION, "mention", True),
    (BARE_MENTION, "mention", True),
    (VALUE, "mention", False),             # the value file passed off as mention
    (VALUE_MARKED, "mention", False),
    (MENTION, "mention-shuf", False),      # the true index is not the shuffled one
    (MENTION + SHUF, "mention-shuf", True),
    (VALUE + SHUF, "mention-shuf", False),
    (MENTION + SHUF, "mention", False),
    ("none+merge=none@file/1", "none", True),
    ("none+merge=none+attach=mention@file/1", "none", False),
    ("rules/3@file/1", "rules-value", True),
    (RULES_MENTION, "rules-value", False),
    (RULES_MENTION, "rules-mention", True),
    ("rules/3@file/1", "rules-mention", False),
    ("<none>", "mention", False),
])
def test_round3_arms_read_the_attach_slot(stamp: str, arm: str, ok: bool) -> None:
    assert f10.stamp_allowed(stamp, ARMS_R3[arm]) is ok


@pytest.mark.parametrize("stamp", [MENTION, VALUE_MARKED, RULES_MENTION, MENTION + SHUF])
def test_round2_arm_configs_refuse_every_attach_stamp(stamp: str) -> None:
    """An arm config without ``attach`` is round 2's: an attach stamp is outside its grammar."""
    for arm in (PURE, {**PURE, "shuffle_seed": "261103|shuf"}, {"stamps": ["rules/3"], "merge": None},
                {"stamp_prefixes": ["llm/2:", "rules/", "none"]}):
        assert f10.stamp_allowed(stamp, arm) is False


def test_normalise_config_refuses_an_unknown_rule() -> None:
    with pytest.raises(f10.Refused, match="attach"):
        cfg(arms={"m": {**PURE, "attach": "document"}})
    assert cfg(arms=ARMS_R3)["arms"]["mention"]["attach"] == "mention"


def test_swap_record_of_a_mention_arm() -> None:
    conf = cfg(arms={"mention": {**ARMS_R3["mention"], "glossary_file_sha256": {"O": "f" * 64}},
                     "value": {**ARMS_R3["value"], "glossary_file_sha256": {"O": "f" * 64}}})
    sw = swap(arm="mention", census_after={"total": 100, "stamps": {MENTION: 90, BARE_MENTION: 10}})
    assert f10.verify_swap_record(sw, conf, "O", "mention")["total"] == 100
    # the value file attached under the mention arm's name: refused on the stamps
    with pytest.raises(f10.Refused, match="does not allow"):
        f10.verify_swap_record(swap(arm="mention"), conf, "O", "mention")
    with pytest.raises(f10.Refused, match="does not allow"):
        f10.verify_swap_record({**sw, "arm": "value"}, conf, "O", "value")


# --------------------------------------------------------------------------- contract with the tools
def _files(tmp: Path) -> dict[str, Path]:
    """A value (pure) file and its rederive --attach mention copy, made by the glossary tool."""
    from ragstack.ingestion.glossary_file import (
        GlossaryRow,
        masked_key,
        rederive_file,
        write_glossary_file,
    )
    from ragstack.ingestion.symbol_glossary import Definition, Glossary, GlossaryEntry, content_key

    llm = Glossary((GlossaryEntry("Zq", (Definition("fade rate", 0),)),), QWEN)
    rules = Glossary((), "rules/3")
    rows = [GlossaryRow(d, content_key(d), len(d), masked_key(d), rules, llm, llm) for d in ("d1", "d2")]
    src = tmp / "round1.jsonl"
    write_glossary_file(src, rows, producer="t", created="2026-10-30T00:00:00Z")
    value, mention = tmp / "value.jsonl", tmp / "mention.jsonl"
    rederive_file(src, value, "llm-only")
    rederive_file(value, mention, attach="mention")
    return {"value": value, "mention": mention}


def test_contract_stamps_from_the_tool(tmp_path: Path) -> None:
    from ragstack.ingestion.glossary_file import read_glossary_file

    f = _files(tmp_path)
    stamp = {a: read_glossary_file(p).rows["d1"].stamped().version for a, p in f.items()}
    assert stamp["value"] == VALUE and stamp["mention"] == MENTION
    assert f10.stamp_allowed(stamp["value"], ARMS_R3["value"])
    assert f10.stamp_allowed(stamp["mention"], ARMS_R3["mention"])
    assert not f10.stamp_allowed(stamp["value"], ARMS_R3["mention"])
    assert not f10.stamp_allowed(stamp["mention"], ARMS_R3["value"])


def test_check_arm_file_attach(tmp_path: Path) -> None:
    f = _files(tmp_path)
    assert f10.check_arm_file_attach(f["mention"], ARMS_R3["mention"]) == "mention"
    assert f10.check_arm_file_attach(f["value"], ARMS_R3["value"]) == "value"
    assert f10.check_arm_file_attach(f["value"], PURE) is None          # a round-2 arm: nothing checked
    with pytest.raises(f10.Refused, match="declares 'mention'"):
        f10.check_arm_file_attach(f["value"], ARMS_R3["mention"])
    with pytest.raises(f10.Refused, match="declares 'value'"):
        f10.check_arm_file_attach(f["mention"], ARMS_R3["value"])
    # a header edited to mention without the marks is refused on the marks
    rows = [json.loads(x) for x in f["value"].read_text().splitlines()]
    rows[0]["config"]["attach_only_with_value"] = False
    forged = tmp_path / "forged.jsonl"
    forged.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    with pytest.raises(f10.Refused, match="disagrees"):
        f10.check_arm_file_attach(forged, ARMS_R3["mention"])


# --------------------------------------------------------------------------- the boost probe
def test_probe_defaults_are_round2s() -> None:
    assert gbp.probe_request() == gbp.PROBE_REQUEST and gbp.PROBE_REQUEST["top_k"] == 10
    rows = [(f"c{i}", "plain", f"S{i} — zzprobe{i}x") for i in range(4)]
    probes, ctx = gbp.plan(rows, declared=None)
    assert len(probes) == 4 and ctx is not None and ctx["request"] == gbp.PROBE_REQUEST
    _, rec = gbp.plan(rows[:2], declared=None)
    assert rec is not None and rec["request"] == {**gbp.PROBE_REQUEST, "query": "<probe token>"}
    assert rec["selection"]["max_glossary_df"] == 10
    for bad in (9, gbp.MAX_GLOSSARIES_CAP + 1):
        with pytest.raises(gbp.ProbeUnavailable):
            gbp.probe_request(bad)


#: a mention-style rendering: every token sits in 12 glossaries (> 10), except none
CROWDED = [(f"m{k}{i}", "plain words", f"T{k} — widetoken{k}") for k in range(4) for i in range(12)]


class WideAPI(FakeAPI):
    """The fake API with a probe index over CROWDED, at any requested depth."""

    def probe(self, body: dict[str, Any]) -> httpx.Response:
        assert body["retrieval_mode"] == "bm25" and body["rerank"] is False
        self.probe_depths = [*getattr(self, "probe_depths", []), body["top_k"]]
        tok = body["query"].lower()
        hits = [c for c, content, g in CROWDED if tok in gbp.glossary_tokens(g)][: body["top_k"]]
        return httpx.Response(200, json={"sources": [{"chunk_id": c, "doc_id": "d", "content": "", "score": 1.0,
                                                      "metadata": {}} for c in hits]})


def test_mention_rendering_is_not_testable_at_10_and_testable_when_widened(tmp_path: Path) -> None:
    api = WideAPI(RANKS)
    assert run(tmp_path / "a", api, probe_chunks=CROWDED, boost_declared=None) == f10.EXIT_OK
    s = json.loads((tmp_path / "a/out/O/armx.summary.json").read_text())
    assert s["manipulation_check"]["glossary_boost"]["status"] == "not_testable"
    api2 = WideAPI(RANKS)
    assert run(tmp_path / "b", api2, probe_chunks=CROWDED, boost_declared=None,
               probe_max_glossaries=20) == f10.EXIT_OK
    bc = json.loads((tmp_path / "b/out/O/armx.summary.json").read_text())["manipulation_check"]["glossary_boost"]
    assert bc["status"] == "active" and bc["n_probes"] == 4 and bc["request"]["top_k"] == 20
    assert all(p["requested"] == 20 and p["served"] == 12 and p["glossary_df"] == 12 for p in bc["probes"])
    assert api2.probe_depths == [20] * 4
    with pytest.raises(f10.Refused, match="probe-max-glossaries"):
        run(tmp_path / "c", WideAPI(RANKS), probe_max_glossaries=5)


# --------------------------------------------------------------------------- the round-3 analysis
import found10_analysis as fa  # noqa: E402

AN3 = {"n_perm": 2000, "n_boot": 500}


def _r3_world(h2_dir: int, shuf_equals: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """6 papers on O, one V and one M_num record each. M_num: mention finds, value does not.
    V: mention − value = ``h2_dir`` on every paper. ``mention-shuf`` copies arm ``shuf_equals``."""
    recs: list[dict[str, Any]] = []
    per: dict[str, Any] = {}
    for j in range(6):
        for s in ("V", "M_num"):
            rid = f"{s}{j}"
            recs.append({"record_id": rid, "corpus": "O", "paper": f"p{j}", "stratum": s,
                         "symbol_is_the_quantity": "yes" if j < 3 else "no"})
            if s == "M_num":
                v, m = 0, 1
            else:
                v, m = (0, 1) if h2_dir > 0 else (1, 0)
            out = {"value": v, "mention": m}
            out["mention-shuf"] = out[shuf_equals]
            per[rid] = {a: {"found": dict.fromkeys(fa.CONDITIONS, x), "proxy": None} for a, x in out.items()}
    return recs, per


def test_r3_family_all_confirmed_hand_checked() -> None:
    recs, per = _r3_world(+1, "value")
    res = fa.analyse_r3(recs, per, AN3, power=False)
    fam = res["family"]["hypotheses"]
    assert res["family"]["order"] == ["H1", "H2", "H3"] and res["family"]["corpora"] == ["O"]
    # H1 on M_num: 6 papers at +1 -> exact one-sided p = 1/64
    assert fam["H1"]["n_records_population"] == 6 and fam["H1"]["strata"] == ["M_num"]
    assert fam["H1"]["p"] == pytest.approx(1 / 64) and (fam["H1"]["gained"], fam["H1"]["lost"]) == (6, 0)
    # H2 on V, two-sided: 6 papers at +1 -> 2/64
    assert fam["H2"]["type"] == "superiority (two-sided)" and fam["H2"]["p"] == pytest.approx(2 / 64)
    assert fam["H2"]["min_attainable_p"] == pytest.approx(2 / 64)
    # H3 on V u M_num, shuf = value: paper sums +2 on 6 papers -> 1/64
    assert fam["H3"]["n_records_population"] == 12 and fam["H3"]["p"] == pytest.approx(1 / 64)
    # Holm over 3: 3 x 1/64 = .046875; 2 x 1/64 = .03125 -> .046875; 1 x 2/64 -> .046875
    for h in ("H1", "H2", "H3"):
        assert fam[h]["p_holm"] == pytest.approx(0.046875) and fam[h]["status"] == "confirmed", h
    assert fam["H2"]["sign"] == "A > B"


def test_r3_two_sided_loss_and_a_gated_member_enter_holm_at_one() -> None:
    recs, per = _r3_world(-1, "mention")
    fam = fa.analyse_r3(recs, per, AN3, power=False)["family"]["hypotheses"]
    # H3: mention == mention-shuf on every record -> no non-zero paper -> gated, p enters as 1.0
    assert fam["H3"]["gate"] == fa.GATE and fam["H3"]["status"] == fa.GATE
    # H2: 6 papers at -1, two-sided 2/64; Holm: H1 3 x 1/64 = .046875 (confirmed),
    # H2 2 x 2/64 = .0625 (not confirmed), H3 1.0
    assert fam["H2"]["p"] == pytest.approx(2 / 64) and fam["H2"]["net_share"] == -1.0
    assert fam["H1"]["p_holm"] == pytest.approx(0.046875) and fam["H1"]["status"] == "confirmed"
    assert fam["H2"]["p_holm"] == pytest.approx(0.0625) and fam["H2"]["status"] == "not confirmed"
    # the two-sided floor, each tail at alpha/2: D = 6, P(G >= 6) = 1/64 = .0156 -> 6/0 reaches .025
    # but not the Holm worst case .05/6 = .0083
    assert fam["H2"]["sign"] is None and fam["H2"]["mde_split_alpha"] == [6, 0]
    assert "mde_split_holm_worst" in fam["H2"] and fam["H2"]["mde_split_holm_worst"] is None
    assert fam["H3"]["p_holm"] == pytest.approx(1.0)


def test_r3_runner_gate_and_secondary_readings() -> None:
    recs, per = _r3_world(+1, "value")
    res = fa.analyse_r3(recs, per, AN3, gated_arms={"mention-shuf|base": "depth short"}, power=False)
    fam = res["family"]["hypotheses"]
    assert fam["H3"]["gate"] == fa.GATE and "depth short" in fam["H3"]["gate_reason"]
    # Holm with H3 at 1.0: H1 3 x 1/64 = .046875, H2 max(.046875, 2 x 2/64) = .0625
    assert fam["H3"]["p_holm"] == 1.0 and fam["H1"]["p_holm"] == pytest.approx(0.046875)
    assert fam["H2"]["p_holm"] == pytest.approx(0.0625) and fam["H2"]["status"] == "not confirmed"
    sec = res["secondary"]
    assert set(sec["family|bm25"]) == {"H1", "H2", "H3"} and "family|base" not in sec
    pooled = sec["contrasts"]["O"]["pooled mention-value (two-sided)"]
    assert pooled["n_records_population"] == 12 and pooled["sign"] == "A > B"
    # rules arms not run: their contrasts are gated, never a null
    assert sec["contrasts"]["O"]["rules-mention - rules-value (two-sided)"]["gate"] == fa.GATE
    split = sec["m_num_split"]
    assert split["symbol-bound"]["n"] == 3 and split["incidental"]["n"] == 3 and split["unrecorded"] == 0
    d = res["descriptives"]
    assert d["found_at_10"]["O"]["M_num"]["base"]["mention"] == {"found": 6, "n": 6, "failed": 0, "rate": 1.0}
    assert d["sizes"]["family_members"] == {"H1": 6, "H2": 6, "H3": 12}


def test_r3_power_is_per_member(tmp_path: Path) -> None:
    recs, per = _r3_world(+1, "value")
    pw = fa.analyse_r3(recs, per, AN3, power=True, power_sims=50)["power"]["realised"]
    assert pw["family_size"] == 3
    assert pw["members"]["H2"]["alternative"] == "two-sided" and pw["members"]["H1"]["alternative"] == "greater"
    assert pw["members"]["H3"]["n_records"] == 12 and pw["members"]["H3"]["n_papers"] == 6


def test_round2_analysis_is_untouched_by_round3() -> None:
    assert fa.DEFAULT_ANALYSIS["family_strata"] == ["S1"] and "round" not in fa.DEFAULT_ANALYSIS
    assert list(fa.DEFAULT_HYPOTHESES) == ["H1", "H2", "H3", "H4"]


def test_analyse_dispatches_on_round(tmp_path: Path) -> None:
    recs, _ = _r3_world(+1, "value")
    q = tmp_path / "q.jsonl"
    q.write_text("".join(json.dumps({**r, "set": "O", "question": "q", "gold_chunks": ["g"]}) + "\n"
                         for r in recs))
    (tmp_path / "out").mkdir()
    pc = tmp_path / "cfg.json"
    pc.write_text(json.dumps({"analysis": {"round": 3, **AN3}}))
    assert f10.main(["analyse", "--private-config", str(pc), "--questions", str(q),
                     "--out", str(tmp_path / "out"), "--no-power"]) == 0
    res = json.loads((tmp_path / "out/results-found10.json").read_text())
    assert res["schema"] == "glossbench-r3-found10-analysis/1"
    assert all(t["status"] == fa.GATE for t in res["family"]["hypotheses"].values())   # nothing measured


def test_the_r3_example_config_is_valid_and_matches_the_defaults() -> None:
    ex = json.loads((Path(__file__).resolve().parent / "examples" / "found10-config-r3.example.json").read_text())
    n = f10.normalise_config(ex)
    assert {a: n["arms"][a]["attach"] for a in n["arms"]} == {
        "value": None, "mention": "mention", "mention-shuf": "mention", "none": None,
        "rules-value": None, "rules-mention": "mention"}
    assert ex["analysis"]["hypotheses"] == fa.R3_HYPOTHESES and ex["analysis"]["round"] == 3


def test_a_mention_arm_runs_and_records_its_rule(tmp_path: Path) -> None:
    conf = cfg(arms={"mention": {**ARMS_R3["mention"], "glossary_file_sha256": {"O": "f" * 64}}})
    sw = swap(arm="mention", census_after={"total": 100, "stamps": {MENTION: 90, BARE_MENTION: 10}})
    assert run(tmp_path / "m", FakeAPI(RANKS, stamp=MENTION), conf=conf, sw=sw, arm="mention") == f10.EXIT_OK
    s = json.loads((tmp_path / "m/out/O/mention.summary.json").read_text())
    assert s["status"] == "complete" and s["census"]["attach"] == "mention"
    # bm25 hits carrying the value file's stamp stop the run: the index does not hold the mention arm
    assert run(tmp_path / "v", FakeAPI(RANKS, stamp=VALUE), conf=conf, sw=sw, arm="mention") == f10.EXIT_REFUSED
    # a round-2 arm's summary is unchanged: no attach key
    assert run(tmp_path / "r2", FakeAPI(RANKS)) == f10.EXIT_OK
    assert "attach" not in json.loads((tmp_path / "r2/out/O/armx.summary.json").read_text())["census"]
