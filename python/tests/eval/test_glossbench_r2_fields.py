"""Round-2 harness, set P: the field-list drafting mode, the papers-OC owner's
format (export and verdict import), the pilot batch and the per-set stratum
quotas.

Synthetic only: a made-up room study, a made-up field list (field names are
generic model quantities), fake gateway and fake CLI transports. No store, no
network, no model, no private file.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.eval import glossbench_r2_support as sup

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

import _glossbench_r2 as g  # noqa: E402
import glossbench_r2_checks as ck  # noqa: E402
import glossbench_r2_draft as dr  # noqa: E402
import glossbench_r2_fields as fl  # noqa: E402
import glossbench_r2_pool as pool  # noqa: E402

PROV = {"commit": "f" * 40, "dirty": False, "untracked": 0}
SERVED = "served-drafter-fields"

# ------------------------------------------------------------------ fixtures


def room_text(i: int) -> str:
    return ("Room study {i}. " + sup.FILLER
            + "Two infected students and twenty-one susceptible classmates shared room {i}. " + sup.FILLER
            + "In the winter case the outdoor-air supply was Qv = 0.{i}5 m3/s for the room. " + sup.FILLER
            + "The ventilation gave {i}.5 air changes per hour in summer. " + sup.FILLER
            + "We took an air density of 1.2 kg/m3 near jetty {i}. " + sup.FILLER
            + "The mouth opening was about 2 cm2 for speaker {i}. " + sup.FILLER).format(i=i)


FIELDS = [
    {"field": "occupant_count", "unit": "person", "definition": "total number of people in the room",
     "terms": ["occupants", "classmates", "students"], "n_terms": 3},
    {"field": "susceptible_count", "unit": "person", "definition": "number of susceptible people sharing the room air",
     "terms": ["susceptible", "susceptibles"]},
    {"field": "index_case_count", "unit": "person", "definition": "number of infectious people in the room",
     "extraction_terms": ["infected", "index case"]},
    {"field": "ventilation_flow_m3_s", "unit": "m3/s", "definition": "outdoor-air supply flow into the whole room",
     "terms": ["ventilation rate", "air flow rate", "outdoor air", "airflow"]},
    {"field": "air_density_kg_m3", "unit": "kg/m3", "definition": "mass density of the room air",
     "terms": ["air density", "density of air"]},
    {"field": "mouth_area_m2", "unit": "m2", "definition": "area of the mouth opening",
     "terms": ["mouth area", "mouth opening"]},
    {"field": "gravity_m_s2", "unit": "m/s2", "definition": "gravitational acceleration",
     "terms": ["gravity", "gravitational acceleration"]},
    {"field": "room_type", "unit": "", "definition": "the kind of room", "terms": ["classroom", "room"],
     "kind": "condition"},
]


def write_fields(path: Path, fields: list[dict[str, Any]] | None = None) -> Path:
    path.write_text(json.dumps({"binding": "synthetic", "fields": fields or FIELDS}))
    return path


def make_cfg(tmp: Path, *, n_papers: int = 5, fields_block: dict[str, Any] | None = None,
             no_locator: tuple[int, ...] = (4,)) -> tuple[Path, dict[str, Any]]:
    data = tmp / "data"
    data.mkdir(parents=True, exist_ok=True)
    rows, texts = [], {}
    for i in range(1, n_papers + 1):
        doc = f"d{i}"
        texts[doc] = room_text(i)
        rows += sup.chunk_rows(doc, texts[doc], f"room-{i}" if i in no_locator else f"PMC10{i}")
    chunks = sup.write_jsonl(data / "chunks.jsonl", rows)
    arms = {a: str(sup.write_arm(data / f"{a}.jsonl", texts)) for a in ("rules", "scoutp", "qwen38p", "qwen36p")}
    fpath = write_fields(data / "fields.json")
    cfg = {"out_dir": str(tmp / "out"),
           "corpora": {"P": {"chunks": [str(chunks)], "expected_chunks": len(rows), "paper_key": "doc_id",
                             "arms": arms, "draft_mode": "fields",
                             "fields": {"path": str(fpath), "min_field_chunks": 2,
                                        "constants": ["gravity_m_s2", "air_density_kg_m3"],
                                        **(fields_block or {})}}},
           "pool": {"n_papers": n_papers, "per_set": {"P": {"s3_per_paper": 6}}},
           "drafter": {"base_url": "http://127.0.0.1:18999/v1", "empty_retry_s": 0.0},
           "verifier": {"cwd": str(tmp / "vcwd")},
           "limits": {"rate_backoffs_s": [0.0, 0.0, 0.0]}}
    path = tmp / "config.json"
    path.write_text(json.dumps(cfg))
    return path, cfg


def build(tmp: Path, **kw: Any) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    path, cfg = make_cfg(tmp, **kw)
    m = pool.build(cfg, "P", g.out_dir(cfg), provenance=lambda: PROV)
    return path, cfg, m


def pool_rows(cfg: dict[str, Any], set_: str = "P") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = [json.loads(x) for x in (Path(cfg["out_dir"]) / f"pool-{set_}.jsonl").read_text().splitlines()]
    return rows[0], rows[1:]


# ------------------------------------------------------------------ a fake field drafter

def fake_field_draft(user: str) -> dict[str, Any]:
    import re

    body = user.split("<<<\n", 1)[1].split("\n>>>", 1)[0]
    field = re.search(r"QUANTITY: (\S+)", user).group(1)  # type: ignore[union-attr]
    none = {"none": True}
    if field == "index_case_count":
        m = re.search(r"Two infected students", body)
        return none if not m else {"question": "How many infectious people were in the room?", "quote": m.group(0),
                                   "quantity": "infected students", "value": "Two", "symbol": "", "case": ""}
    if field == "susceptible_count":
        m = re.search(r"twenty-one susceptible classmates", body)
        return none if not m else {"question": "How many susceptible people shared the room air?",
                                   "quote": m.group(0), "quantity": "susceptible classmates", "value": "twenty-one",
                                   "symbol": "", "case": ""}
    if field == "ventilation_flow_m3_s":
        m = re.search(r"outdoor-air supply was Qv = (0\.\d5 m3/s)", body)
        if m:
            return {"question": "What was the outdoor-air supply flow into the whole room in winter?",
                    "quote": m.group(0), "quantity": "outdoor-air supply", "value": m.group(1), "symbol": "Qv",
                    "case": "winter case"}
        m = re.search(r"gave (\d\.5 air changes per hour)", body)
        return none if not m else {"question": "What was the outdoor-air supply flow into the whole room in summer?",
                                   "quote": m.group(0), "quantity": "air changes", "value": m.group(1),
                                   "symbol": "", "case": "summer"}
    if field == "air_density_kg_m3":
        m = re.search(r"an air density of (1\.2 kg/m3)", body)
        return none if not m else {"question": "What mass density of the room air did the authors take?",
                                   "quote": m.group(0), "quantity": "air density", "value": m.group(1),
                                   "symbol": "", "case": ""}
    if field == "mouth_area_m2":
        m = re.search(r"mouth opening was (about 2 cm2)", body)
        return none if not m else {"question": "What was the area of the mouth opening?", "quote": m.group(0),
                                   "quantity": "mouth opening", "value": m.group(1), "symbol": "", "case": ""}
    return none


class Resp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.status_code, self._p, self.text = 200, payload, json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return self._p


class FieldGateway:
    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

    async def post(self, url: str, json: dict[str, Any]) -> Resp:  # noqa: A002
        self.bodies.append(json)
        await asyncio.sleep(0)
        content = __import__("json").dumps(fake_field_draft(json["messages"][-1]["content"]))
        return Resp({"model": SERVED, "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                     "usage": {"prompt_tokens": 50, "completion_tokens": 20}})

    async def aclose(self) -> None:
        pass


class OkCLI:
    def __call__(self, cmd: list[str], *, input: str, capture_output: bool, text: bool, cwd: str,  # noqa: A002
                 timeout: int) -> Any:
        from types import SimpleNamespace

        v = {"supports": True, "single_reading": True, "same_case": True, "symbol_unique": True, "reason": "ok"}
        out = {"result": json.dumps(v), "is_error": False, "total_cost_usd": 0.0,
               "modelUsage": {"claude-opus-5-5": {"inputTokens": 400, "outputTokens": 20}}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(out), stderr="")


def run(path: Path, *args: str, gw: Any = None, cli: Any = None) -> int:
    return dr.main(["--private-config", str(path), *args], provenance=lambda: PROV,
                   http_factory=(lambda: gw) if gw else None, cli_runner=cli)


def full_chain(path: Path, *extra: str) -> FieldGateway:
    gw = FieldGateway()
    assert run(path, "preflight", "--stage", "draft", "--set", "P", *extra) == 0
    assert run(path, "draft", "--set", "P", "--notice-acknowledged", *extra, gw=gw) == 0
    assert run(path, "check", "--set", "P", *extra) == 0
    assert run(path, "preflight", "--stage", "verify", "--set", "P", *extra) == 0
    assert run(path, "verify", "--set", "P", "--notice-acknowledged", *extra, cli=OkCLI()) == 0
    assert run(path, "summary", "--set", "P", *extra) == 0
    return gw


def jsonl(p: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


# ------------------------------------------------------------------ the field list

def test_field_list_schema(tmp_path: Path) -> None:
    fs, sha = fl.read_field_list(write_fields(tmp_path / "f.json"))
    assert len(sha) == 64 and [f.field for f in fs][:2] == ["occupant_count", "susceptible_count"]
    by = {f.field: f for f in fs}
    assert by["index_case_count"].terms == ("infected", "index case")          # the extraction_terms alias
    assert by["room_type"].kind == "condition"
    # JSONL, flags as optional booleans
    jl = tmp_path / "f.jsonl"
    jl.write_text("\n".join(json.dumps({**f, "in_set": True, "constant": i == 0}) for i, f in enumerate(FIELDS[:3])))
    fs2, _ = fl.read_field_list(jl)
    assert fs2[0].constant is True and fs2[1].constant is False and fs2[0].in_set is True
    for bad, msg in (([FIELDS[0], FIELDS[0]], "named twice"), ([{**FIELDS[0], "terms": []}], "terms"),
                     ([{**FIELDS[0], "definition": ""}], "empty definition"),
                     ([{**FIELDS[0], "in_set": "yes"}], "boolean"), ([{**FIELDS[0], "kind": "rate"}], "kind"),
                     # the file's own count must match its list: a truncated term list is refused
                     ([{**FIELDS[0], "n_terms": 4}], "n_terms 4 differs from len\\(terms\\) 3"),
                     ([{**FIELDS[0], "n_terms": "3"}], "n_terms")):
        with pytest.raises(SystemExit, match=msg):
            fl.read_field_list(write_fields(tmp_path / "bad.json", bad))


#: the 26 field names of the field binding (generic model quantities; definitions here are made up)
NAMES26 = """air_density_kg_m3 air_dynamic_viscosity_pa_s air_exchange_rate_s breathing_flow_m3_s clearance_fraction
deactivation_rate_s effective_radius_m entrainment_coefficient exposure_time_s filter_efficiency floor_area_m2
gravity_m_s2 index_case_count initial_concentration_quanta_m3 mask_penetration mouth_area_m2 neighbor_distance_m
occupant_count particle_density_kg_m3 quanta_concentration_exhaled_m3 recirculation_flow_m3_s
relative_transmissibility risk_tolerance room_height_m susceptible_count ventilation_flow_m3_s""".split()
NEW_GROUND10 = """clearance_fraction exposure_time_s filter_efficiency initial_concentration_quanta_m3
neighbor_distance_m occupant_count recirculation_flow_m3_s risk_tolerance susceptible_count
ventilation_flow_m3_s""".split()


def _held_set(tmp: Path, names: list[str]) -> Path:
    p = tmp / "held.jsonl"
    p.write_text("".join(json.dumps({"field": n, "question": "synthetic"}) + "\n" for n in names for _ in range(2))
                 + json.dumps({"question": "no field"}) + "\n")
    return p


def test_in_set_is_computed_and_the_default_constants(tmp_path: Path) -> None:
    """26 fields, a held set naming 13 of them: in_set is computed from the records (the
    file's flag is ignored), the default constants are all in the file, gravity is both
    constant and in_set, and the new-ground set is exactly 10 fields."""
    held = sorted(set(NAMES26) - set(NEW_GROUND10) - {"air_density_kg_m3", "air_dynamic_viscosity_pa_s",
                                                      "particle_density_kg_m3"})
    assert len(held) == 13
    fields = [{"field": n, "unit": "", "definition": f"made-up {n}", "terms": [n.split("_")[0]],
               "in_set": True} for n in NAMES26]       # the file says in_set everywhere: ignored
    fc = {**fl.FIELD_DEFAULTS, "path": str(write_fields(tmp_path / "f26.json", fields)),
          "expect_in_set": {"true": 13, "false": 13}}
    res = fl.resolve_fields(fc, str(_held_set(tmp_path, held)))
    by = {f.field: f for f in res["fields"]}
    assert res["in_set"]["counts"] == {"true": 13, "false": 13, "unknown": 0}
    assert res["in_set"]["records_sha256"] == g.file_sha(tmp_path / "held.jsonl")
    assert res["in_set"]["records_without_key"] == 1
    assert set(fl.DEFAULT_CONSTANTS) <= set(by) and res["constants"] == sorted(fl.DEFAULT_CONSTANTS)
    assert by["gravity_m_s2"].constant and by["gravity_m_s2"].in_set is True
    assert res["new_ground"] == sorted(NEW_GROUND10)
    with pytest.raises(SystemExit, match="expect_in_set"):
        fl.resolve_fields({**fc, "expect_in_set": {"true": 12, "false": 14}}, str(tmp_path / "held.jsonl"))
    with pytest.raises(SystemExit, match="constants not in the field list"):
        fl.resolve_fields({**fc, "constants": ["gravity_m_s2", "speed_of_light"]}, str(tmp_path / "held.jsonl"))
    # without held-set records, the file's own flag stands
    assert fl.resolve_fields({**fc, "expect_in_set": None})["in_set"]["counts"]["true"] == 26


def test_field_mode_is_set_p_only_and_names_outside_the_list_are_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="set P only"):
        fl.field_config({"draft_mode": "fields", "fields": {"path": "x"}}, "O")
    with pytest.raises(SystemExit, match="unknown keys"):
        fl.field_config({"draft_mode": "fields", "fields": {"path": "x", "quota": 3}}, "P")
    fs, _ = fl.read_field_list(write_fields(tmp_path / "f.json"))
    with pytest.raises(SystemExit, match="not in the field list"):
        fl.planned_names(fs, {"priority_fields": ["occupant_count", "co2_ppm"]})
    with pytest.raises(SystemExit, match="conditions"):
        fl.planned_names(fs, {"priority_fields": ["room_type"]})


# ------------------------------------------------------------------ quotas

def test_allocation_weights_caps_and_redistribution() -> None:
    a = fl.allocate(12, {"new": 2.0, "old": 1.0, "const": 1.0}, {"new": 100, "old": 100, "const": 2})
    assert {k: v["stated"] for k, v in a.items()} == {"new": 6, "old": 3, "const": 3}
    assert a["const"]["quota"] == 2                                   # capped
    assert a["new"]["quota"] + a["old"]["quota"] == 10                # the leftover is placed
    assert a["new"]["quota"] == 7 and a["old"]["quota"] == 3          # by weight (2:1 of the 1 left -> new)
    b = fl.allocate(10, {"x": 1.0, "y": 1.0}, {"x": 1, "y": 3})
    assert b["x"]["quota"] == 1 and b["y"]["quota"] == 3              # nobody has room: 4 placed of 10


def _specs(tmp_path: Path) -> tuple[list[fl.FieldSpec], dict[str, Any]]:
    fs, _ = fl.read_field_list(write_fields(tmp_path / "f.json"))
    fs = [fl.FieldSpec(f.field, f.unit, f.definition, f.terms, "constant" if f.field == "air_density_kg_m3" else f.kind,
                       f.field not in ("occupant_count", "susceptible_count"), f.field == "air_density_kg_m3")
          for f in fs]
    return fs, {**fl.FIELD_DEFAULTS, "constants": ["air_density_kg_m3"]}


def _rows(spec: dict[tuple[str, str], dict[str, int]]) -> list[dict[str, Any]]:
    """Pool rows: (paper, stratum) -> {field: number of candidate chunks}; one chunk per candidate."""
    rows = []
    for (p, s), per in spec.items():
        for f, n in per.items():
            rows += [{"chunk_id": f"{p}-{s}-{f}-{i}", "paper": p, "stratum": s, "field_candidates": [f]}
                     for i in range(n)]
    return rows


def test_plan_excludes_conditions_caps_constants_and_weights_new_ground(tmp_path: Path) -> None:
    fs, fc = _specs(tmp_path)
    names = [f.field for f in fs]
    rows = _rows({(p, "S3"): dict.fromkeys(names, 20) for p in ("a", "b", "c")})
    targets = fl.cell_targets(["a", "b", "c"], {"S1": 0, "S2": 0, "S3": 12})
    plan, jobs = fl.plan_strata(fs, fc, rows, targets, "seed")
    assert "room_type" not in plan["fields"] and plan["excluded_conditions"] == ["room_type"]
    assert not any("room_type" in v for v in jobs.values())
    assert plan["fields"]["air_density_kg_m3"]["realised"] == 2                     # capped over the whole set
    assert plan["fields"]["occupant_count"]["weight"] == 2.0 and plan["fields"]["mouth_area_m2"]["weight"] == 1.0
    assert plan["fields"]["occupant_count"]["new_ground"] and not plan["fields"]["mouth_area_m2"]["new_ground"]
    assert plan["fields"]["occupant_count"]["realised"] > plan["fields"]["mouth_area_m2"]["realised"]
    assert plan["realised"] == 36 and plan["weights"] == {"new_ground": 2.0, "other": 1.0}
    cell = plan["cells"]["a"]["S3"]
    assert cell["target"] == 12 and cell["realised"] == 12 and sum(cell["fields"].values()) == 12
    assert cell["stated"]["occupant_count"] > cell["stated"]["mouth_area_m2"]               # stated by weight
    plan1, _ = fl.plan_strata(fs, {**fc, "weight_new_ground": 1.0}, rows, targets, "seed")
    assert plan1["fields"]["occupant_count"]["weight"] == 1.0
    assert (plan, jobs) == fl.plan_strata(fs, fc, rows, targets, "seed")             # seeded


def test_stratum_quotas_per_paper_and_shortfall_stays_in_its_stratum(tmp_path: Path) -> None:
    """Each paper targets s1/s2/s3_per_paper jobs by chunk stratum. A paper short of S3
    candidates records the shortfall; it moves to other papers' S3 cells only, never
    to S1/S2, and what nobody can take is 'unplaced'."""
    fs, fc = _specs(tmp_path)
    rows = _rows({("a", "S3"): {"index_case_count": 1}, ("b", "S3"): {"occupant_count": 4, "mouth_area_m2": 4},
                  ("a", "S2"): {"ventilation_flow_m3_s": 9}, ("b", "S2"): {"ventilation_flow_m3_s": 9}})
    targets = fl.cell_targets(["a", "b"], {"S1": 0, "S2": 2, "S3": 6})
    plan, jobs = fl.plan_strata(fs, fc, rows, targets, "seed")
    a3, b3 = plan["cells"]["a"]["S3"], plan["cells"]["b"]["S3"]
    assert a3["target"] == 6 and a3["shortfall"] == 5 and a3["realised"] == 1
    assert b3["realised"] == 8 and b3["assigned"] == 8                   # 6 + 2 of a's 5 (b has 8 candidates)
    assert plan["strata"]["S3"] == {"target": 12, "shortfall": 5, "unplaced": 3, "realised": 9}
    assert plan["cells"]["a"]["S2"]["realised"] == 2 and plan["cells"]["b"]["S2"]["realised"] == 2   # S2 unchanged
    s3_jobs = sum(len(v) for k, v in jobs.items() if "-S3-" in k)
    assert s3_jobs == 9 and sum(len(v) for k, v in jobs.items() if "-S2-" in k) == 4
    # the global quota of 2: a's shortfall of 1 goes to b -> 4 S3 jobs; the quota of 6 placed 9
    less, _ = fl.plan_strata(fs, fc, rows, fl.cell_targets(["a", "b"], {"S1": 0, "S2": 2, "S3": 2}), "seed")
    assert less["strata"]["S3"]["realised"] == 4 and less["cells"]["b"]["S3"]["assigned"] == 3
    # scaled to a total (target_jobs / a pilot's N): the cell weights, by largest remainder
    t = fl.cell_targets(["a", "b"], {"S1": 0, "S2": 2, "S3": 6}, 8)
    assert sum(t.values()) == 8 and t[("a", "S3")] == 3 and t[("a", "S2")] == 1


# ------------------------------------------------------------------ units and the prefilter

@pytest.mark.parametrize("value,unit,verdict", [
    ("about 2 cm2", "m2", "ok"),                      # printed, not converted: cm2 is an area
    ("0.15 m3/s", "m3/s", "ok"), ("10 L/s", "m3/s", "ok"), ("1.8e-5 Pa s", "Pa*s", "ok"),
    ("9.81 m s−2", "m/s2", "ok"), ("30%", "fraction", "ok"), ("2 h", "s", "ok"),
    ("Two", "person", "ok"), ("twenty-one", "person", "ok"),
    ("6 air changes per hour", "m3/s", "related"),    # ACH x volume = flow: a flag
    ("6 h-1", "m3/s", "related"),
    ("3 knots", "m/s", "unknown"),                    # outside the map: a flag
    ("3 m", "furlongs", "field_unknown"),
    ("5 kg", "m", "incompatible"), ("0.5", "m", "incompatible"),
])
def test_unit_check(value: str, unit: str, verdict: str) -> None:
    assert fl.unit_check(value, unit) == verdict


def test_prefilter_keeps_prose_counts_and_needs_a_number(tmp_path: Path) -> None:
    fs = {f.field: f for f in fl.read_field_list(write_fields(tmp_path / "f.json"))[0]}
    prose = "Two infected students and twenty-one susceptible classmates shared the room."
    assert fl.prefilter(prose, fs["susceptible_count"]) and fl.prefilter(prose, fs["index_case_count"])
    assert not fl.prefilter("The susceptible classmates shared the room.", fs["susceptible_count"])   # no number
    assert fl.prefilter("The supply was 0.3 m3/s.", fs["ventilation_flow_m3_s"])         # a unit of its dimension
    assert fl.prefilter("The room had 4 air changes per hour.", fs["ventilation_flow_m3_s"])   # a related one
    assert not fl.prefilter("The supply was 0.3 kg/m3.", fs["ventilation_flow_m3_s"])
    assert not fl.prefilter("Seven occupants stayed.", fs["mouth_area_m2"])


# ------------------------------------------------------------------ the term-leak guard

def test_term_leak_uses_the_wording_proxys_stemming() -> None:
    """'ventilation rate' (a term) vs 'ventilation rates' (the question) is caught: the
    comparison is on the wording proxy's stems. Without stemming 'rates' would not
    match and the share would be 0.5, under the strict > 0.5 threshold."""
    terms, definition = ["ventilation rate", "airflow"], "outdoor air supplied to the whole room"
    for q in ("What were the ventilation rates of the room in winter?",
              "What was the ventilation rate of the room in winter?"):
        share, n = fl.term_leak(q, terms, definition, case="winter")
        assert share == 1.0 and n == 2, q
    # the definition's own words and the stated case are excused
    assert fl.term_leak("What outdoor air was supplied to the whole room in winter?", terms, definition,
                        case="winter") == (0.0, 0)


def test_term_leak_catches_ach_and_air_changes_per_hour() -> None:
    """An air-exchange-rate field whose term list names the rate as 'air changes per
    hour' and 'ACH': a question that says either is caught (the share of its own
    content words that come from the term list is above 0.5). The definition here
    does not use those words; a definition that does excuses them (by design: the
    drafter is told to use the definition's words), and the guard cannot fire on them."""
    terms = ["air changes per hour", "ACH", "air exchange rate", "ventilation rate"]
    definition = "the fraction of the room volume replaced by outdoor supply in unit time"
    for q in ("What was the air changes per hour of the room?", "How many air changes per hour were given?",
              "What ACH did the room have?", "Which ach did the paper report?"):
        share, n = fl.term_leak(q, terms, definition)
        assert share > 0.5 and n >= 1, (q, share, n)
    # exactly 0.5: one term word against one other content word (layer 1 rejects at >= 0.5)
    assert fl.term_leak("What was the ACH in the classroom?", terms, definition) == (0.5, 2)
    # the same words in the definition are excused, and the guard is silent
    excusing = "air changes per hour of the room"
    assert fl.term_leak("What was the air changes per hour of the room?", terms, excusing)[0] == 0.0


# ------------------------------------------------------------------ layer 1 in fields mode

def _paper(tmp_path: Path) -> tuple[g.Corpus, ck.PaperIndex, dict[str, fl.FieldSpec]]:
    rows = [g.parse_chunk(r, "t") for r in sup.chunk_rows("d1", room_text(1), "PMC101")]
    c = g.build_corpus(rows, "doc_id")
    return c, ck.PaperIndex(c, 200), {f.field: f for f in fl.read_field_list(write_fields(tmp_path / "f.json"))[0]}


def _chunk(c: g.Corpus, text: str) -> g.ChunkRow:
    return next(x for x in c.paper_chunks("d1") if text in x.content)


def _l1(c: Any, idx: Any, d: dict[str, Any], chunk: g.ChunkRow, spec: fl.FieldSpec, stratum: str = "S2",
        **cfg: Any) -> dict[str, Any]:
    return ck.layer1({**d, "binding_terms": list(spec.terms)}, None, stratum=stratum, paper="d1",
                     source_chunk=chunk, idx=idx, cfg=cfg, field=spec)


def test_layer1_fields_mode(tmp_path: Path) -> None:
    c, idx, fs = _paper(tmp_path)
    vent = fs["ventilation_flow_m3_s"]
    ch = _chunk(c, "Qv = 0.15 m3/s")
    good = {"question": "What was the outdoor-air supply flow into the whole room in winter?",
            "quote": "outdoor-air supply was Qv = 0.15 m3/s", "quantity": "outdoor-air supply",
            "value": "0.15 m3/s", "symbol": "Qv", "case": "winter case"}
    r = _l1(c, idx, good, ch, vent)
    assert r["verdict"] == "pass" and r["record_stratum"] == "S2" and r["unit_check"] == "ok", r
    # binding terms are the field's (4 terms, one of 3 words): the model-term checks do not apply
    assert "binding_terms_invalid" not in r["reasons"]
    # the printed unit
    assert "unit_incompatible" in _l1(c, idx, {**good, "value": "0.15"}, ch, vent)["reasons"]
    # the term-leak guard
    leaky = {**good, "question": "What was the ventilation rate, airflow or air flow rate in winter?"}
    r = _l1(c, idx, leaky, ch, vent)
    assert "term_leak" in r["reasons"] and r["term_leak"] > 0.5
    assert "term_leak" not in _l1(c, idx, leaky, ch, vent, term_leak_threshold=1.01)["reasons"]
    # ACH against a volume flow: flagged, not rejected; no symbol -> a words-only (S3) record
    ach = _chunk(c, "1.5 air changes per hour")
    d = {"question": "What was the outdoor-air supply flow into the whole room in summer?",
         "quote": "gave 1.5 air changes per hour", "quantity": "air changes", "value": "1.5 air changes per hour",
         "symbol": "", "case": "summer"}
    r = _l1(c, idx, d, ach, vent, stratum="S1")
    assert r["verdict"] == "pass" and "unit_dimension_differs" in r["flags"] and r["record_stratum"] == "S3"
    # no symbol, yet the quote states the value through one: not words-only
    r = _l1(c, idx, {**good, "symbol": ""}, ch, vent)
    assert "s3_value_statement" in r["reasons"]
    # the value as printed: about 2 cm2 for an area in m2
    area = {"question": "What was the area of the mouth opening?", "quote": "mouth opening was about 2 cm2",
            "quantity": "mouth opening", "value": "about 2 cm2", "symbol": "", "case": ""}
    r = _l1(c, idx, area, _chunk(c, "about 2 cm2"), fs["mouth_area_m2"])
    assert r["verdict"] == "pass" and r["unit_check"] == "ok"
    # prose counts stay eligible
    prose = {"question": "How many susceptible people shared the room air?",
             "quote": "twenty-one susceptible classmates", "quantity": "susceptible classmates",
             "value": "twenty-one", "symbol": "", "case": ""}
    assert _l1(c, idx, prose, _chunk(c, "twenty-one susceptible"), fs["susceptible_count"])["verdict"] == "pass"


def test_field_prompt_is_blind_and_names_the_field(tmp_path: Path) -> None:
    spec = fl.FieldSpec("ventilation_flow_m3_s", "m3/s", "outdoor-air supply flow into the whole room",
                        ("zorbex rate", "qq flux"))
    msgs = ck.field_draft_messages("In winter Qv = 0.15 m3/s was supplied.", spec)
    text = json.dumps(msgs)
    assert "QUANTITY: ventilation_flow_m3_s" in text and "outdoor-air supply flow" in text and "UNIT: m3/s" in text
    assert "zorbex" not in text and "qq flux" not in text            # the term list is not the question's wording
    assert "Tokens that state a value" not in text                   # no symbol hint
    low = text.casefold()
    for banned in ("glossary", "rules", "qwen", "scout", "frontier"):
        assert banned not in low
    assert '"case"' in msgs[0]["content"] and "never convert" in msgs[0]["content"].casefold()
    assert ck.parse_draft(json.dumps({"question": "q", "quote": "x", "quantity": "y", "value": "1", "symbol": "",
                                      "case": "winter"}), "fields")[0]["case"] == "winter"
    assert ck.parse_draft('{"question": "q"}', "fields")[0] is None


# ------------------------------------------------------------------ the pool in fields mode

def test_pool_fields_mode(tmp_path: Path) -> None:
    _, cfg, m = build(tmp_path)
    header, rows = pool_rows(cfg)
    assert header["draft_mode"] == "fields"
    assert header["fields_sha256"] == g.file_sha(cfg["corpora"]["P"]["fields"]["path"])
    plan = header["field_plan"]
    assert "room_type" not in plan["fields"] and plan["excluded_conditions"] == ["room_type"]
    jobs = [(r["chunk_id"], f) for r in rows for f in r["field_jobs"]]
    per = {f: sum(1 for _, x in jobs if x == f) for f in plan["fields"]}
    assert per == {f: v["realised"] for f, v in plan["fields"].items()}      # the plan is what is drafted
    assert per["air_density_kg_m3"] <= 2 and plan["fields"]["air_density_kg_m3"]["constant"]
    # the per-set S3 quota is applied in fields mode: 6 S3 jobs per paper where candidates allow
    assert plan["per_paper"] == {"S1": 12, "S2": 2, "S3": 6}
    stratum = {r["chunk_id"]: r["stratum"] for r in rows}
    for p, cells in plan["cells"].items():
        assert cells["S3"]["target"] == 6
        n3 = sum(1 for cid, _ in jobs if stratum[cid] == "S3" and cid.startswith(p + "-"))
        assert n3 == cells["S3"]["realised"] == sum(cells["S3"]["fields"].values())
    assert plan["strata"]["S3"]["realised"] == 6 * len(plan["cells"])
    assert plan["strata"]["S1"]["shortfall"] == 12 * len(plan["cells"])          # no S1 here: recorded
    assert not any("room_type" in r["field_jobs"] for r in rows)
    assert all(r["sampled"] == bool(r["field_jobs"]) for r in rows)
    assert any(r["stratum"] == "S3" for r in rows if r["field_jobs"])         # prose / words-only chunks kept
    mp = m["fields"]["plan"]
    assert mp["strata"] == plan["strata"] and m["fields"]["jobs"] == len(jobs)
    assert sorted(mp["cells"]) == [f"p{i:02d}" for i in range(1, len(plan["cells"]) + 1)]     # counts, no paper ids
    assert list(mp["cells"].values()) == [plan["cells"][p] for p in sorted(plan["cells"])]
    for st in g.string_values(mp):
        assert not st.startswith("d") or st not in plan["cells"]
    assert {d["field"] for d in header["field_specs"]} == set(plan["fields"])


def test_per_set_stratum_quotas(tmp_path: Path) -> None:
    _, cfg, _ = sup.make_config(tmp_path / "a", pool={"per_set": {"O": {"s3_per_paper": 3}}})
    m = pool.build(cfg, "O", g.out_dir(cfg), provenance=lambda: PROV)
    header = json.loads((Path(cfg["out_dir"]) / "pool-O.jsonl").read_text().splitlines()[0])
    assert header["quotas_per_paper"] == {"s1_per_paper": 2, "s2_per_paper": 1, "s3_per_paper": 3}
    assert m["design"]["quotas_per_paper"]["s3_per_paper"] == 3 and m["design"]["quotas_overridden"] == ["s3_per_paper"]
    _, cfg2, _ = sup.make_config(tmp_path / "b", pool={"per_set": {"P": {"s3_per_paper": 3}}})
    m2 = pool.build(cfg2, "O", g.out_dir(cfg2), provenance=lambda: PROV)
    assert m2["design"]["quotas_per_paper"]["s3_per_paper"] == 1                # O inherits the global value
    assert pool.pool_config(cfg2, "P")["s3_per_paper"] == 3
    with pytest.raises(SystemExit, match="per_set"):
        pool.pool_config({"pool": {"per_set": {"P": {"n_papers": 3}}}})


# ------------------------------------------------------------------ drafting, export, verdicts

def test_fields_chain_and_partner_export(tmp_path: Path) -> None:
    path, cfg, _ = build(tmp_path)
    gw = full_chain(path)
    out = Path(cfg["out_dir"])
    header, _ = pool_rows(cfg)
    drafts = jsonl(out / "draft-P.jsonl")
    assert drafts and all(d["field"] in header["field_plan"]["fields"] for d in drafts)
    for b in gw.bodies:                                                # blind, and the terms are withheld
        t = json.dumps(b["messages"]).casefold()
        assert "glossary" not in t and "tokens that state a value" not in t and "classmates\", " not in t
    ok = [d for d in drafts if d["draft"] and not d["draft"].get("none")]
    spec_terms = {f["field"]: f.get("terms") or f.get("extraction_terms") for f in FIELDS}
    assert ok and all(d["draft"]["binding_terms"] == spec_terms[d["field"]] for d in ok)
    acc = jsonl(out / "accepted-P.jsonl")
    assert acc and len({a["record_id"] for a in acc}) == len(acc)
    assert all(a["pilot"] is False and a["model"] == SERVED and len(a["prompt_sha256"]) == 64 for a in acc)
    words = [a for a in acc if a["words_only"]]
    assert words and all(a["stratum"] == "S3" and a["symbol"] == "" for a in words)   # S3 per record
    assert any(a["value"] == "about 2 cm2" for a in acc)                              # as printed
    assert any("unit_dimension_differs" in a["flags"] for a in acc)
    s = json.loads((out / "acceptance-summary-P.json").read_text())
    assert s["fields"]["words_only_accepted"] == len(words) and s["draft_mode"] == "fields"
    # the partner's format
    exp = tmp_path / "export.jsonl"
    assert run(path, "export-partner", "--set", "P", str(exp)) == 0
    rows = jsonl(exp)
    on_record = json.loads((out / "partner-export-P.json").read_text())
    unresolved = sum(1 for a in acc if a["doc_id"] == "d4")
    assert unresolved and on_record["refused"] == {"unresolved_locator": unresolved}
    assert len(rows) == len(acc) - unresolved == on_record["exported"]
    for r in rows:
        assert set(r) == {"id", "question", "source", "quote", "field", "case", "model", "prompt_sha256", "value"}
        assert r["source"]["locator"] == f"PMC10{r['source']['doc_id'][1:]}" and r["model"] == SERVED
    assert any(r["case"] == "winter case" for r in rows)
    # candidates = the layer-1 passes
    cand = tmp_path / "cand.jsonl"
    assert run(path, "export-partner", "--set", "P", str(cand), "--candidates") == 0
    assert len(jsonl(cand)) >= len(rows)
    # the owner's verdicts: label, checks
    assert run(path, "export-partner", "--set", "P", str(exp)) == 0
    ids = [r["id"] for r in rows]
    verdicts = {"records": [{"id": ids[0], "verdict": "reject", "reason": "scripted: unit", "basis": "scripted"}]
                + [{"id": i, "verdict": "reject" if n == 0 else "accept", "reason": "wrong case" if n == 0 else "",
                    "basis": "sampled"} for n, i in enumerate(ids[1:5])],
                "sample": {"n": 4, "errors": 1, "seed": "partner-seed"}}
    vp = tmp_path / "verdicts.json"
    vp.write_text(json.dumps(verdicts))
    assert run(path, "import-partner-verdicts", "--set", "P", str(vp)) == 0
    res = json.loads((out / "partner-verdicts-P.json").read_text())
    lo, hi = dr.wilson(1, 4)
    assert res["status_label"] == (f"scripted-clean, judged sample of 4, error rate 25.0% "
                                   f"(95% CI {100 * lo:.1f}–{100 * hi:.1f})")
    assert res["counts"]["scripted_rejects"] == 1 and res["sample"]["seed"] == "partner-seed"
    for change, msg in ((lambda v: v["records"].append({"id": "P-unknown", "verdict": "accept", "basis": "sampled"}),
                         "not in our export"),
                        (lambda v: v["sample"].update(errors=2), "disagree"),
                        (lambda v: v["records"][0].update(reason=""), "needs a reason"),
                        (lambda v: v["sample"].pop("seed"), "seed")):
        bad = json.loads(json.dumps(verdicts))
        change(bad)
        vp.write_text(json.dumps(bad))
        with pytest.raises(SystemExit, match=msg):
            run(path, "import-partner-verdicts", "--set", "P", str(vp))


def test_locator_resolution() -> None:
    assert fl.locator("d1", [{"source_path": "PMC123#table-1"}]) == "PMC123"
    assert fl.locator("d1", [{"doi": "10.1234/abc.5."}]) == "10.1234/abc.5"
    assert fl.locator("PMC999", [{}]) == "PMC999"
    assert fl.locator("d1", [{"source_path": "room-1"}]) is None


def test_changed_field_list_is_refused_on_load_and_resume(tmp_path: Path) -> None:
    path, cfg, _ = build(tmp_path)
    out = Path(cfg["out_dir"])
    (out / "PAUSE").touch()
    assert run(path, "preflight", "--stage", "draft", "--set", "P") == 0
    assert run(path, "draft", "--set", "P", "--notice-acknowledged", gw=FieldGateway()) == 75
    st = json.loads((out / "draft-state-P.json").read_text())
    assert st["fields_sha256"] == g.file_sha(cfg["corpora"]["P"]["fields"]["path"])
    (out / "PAUSE").unlink()
    write_fields(Path(cfg["corpora"]["P"]["fields"]["path"]), FIELDS[:-1])
    with pytest.raises(SystemExit, match="field list differs"):
        run(path, "preflight", "--stage", "draft", "--set", "P")
    with pytest.raises(SystemExit, match="field list differs"):
        run(path, "draft", "--set", "P", "--notice-acknowledged", "--resume", gw=FieldGateway())


def test_open_mode_config_against_a_fields_pool_is_refused(tmp_path: Path) -> None:
    path, cfg, _ = build(tmp_path)
    cfg["corpora"]["P"].pop("draft_mode")
    cfg["corpora"]["P"].pop("fields")
    path.write_text(json.dumps(cfg))
    with pytest.raises(SystemExit, match="draft_mode differs"):
        run(path, "preflight", "--stage", "draft", "--set", "P")


def test_pilot_is_separate_and_marked(tmp_path: Path) -> None:
    path, cfg, _ = build(tmp_path)
    out = Path(cfg["out_dir"])
    gw = full_chain(path, "--pilot", "6", "--papers", "2")
    drafts = jsonl(out / "draft-P-pilot.jsonl")
    jobs = {(d["chunk_id"], d["field"]) for d in drafts}
    assert 0 < len(jobs) <= 6 and len({d["paper"] for d in drafts}) <= 2
    assert all(d["pilot"] is True and d["record_id"].startswith("P-pilot-") for d in drafts)
    assert not (out / "draft-P.jsonl").exists() and not (out / "accepted-P.jsonl").exists()
    acc = jsonl(out / "accepted-P-pilot.jsonl")
    assert acc and all(a["pilot"] is True for a in acc)
    pf = json.loads((out / "preflight-draft-P-pilot.json").read_text())["plan"]
    assert pf["pilot"]["records"] == 6 and pf["pilot"]["plan"]["realised"] == len(jobs)
    assert gw.bodies
    exp = tmp_path / "pilot.jsonl"
    run(path, "export-partner", "--set", "P", str(exp), "--pilot", "6", "--papers", "2")
    assert all(r["pilot"] is True for r in jsonl(exp))
    # the default pilot size
    r = dr.Run(cfg, g.file_sha(path), out, "P", provenance=lambda: PROV, pilot=(dr.PILOT_DEFAULT_RECORDS, 2))
    r.load()
    assert dr.PILOT_DEFAULT_RECORDS == 50 and dr.PILOT_DEFAULT_PAPERS == 3
    assert len({j["paper"] for j in r.sampled}) <= 2


def test_a_job_outside_the_field_list_is_refused(tmp_path: Path) -> None:
    path, cfg, _ = build(tmp_path)
    p = Path(cfg["out_dir"]) / "pool-P.jsonl"
    rows = [json.loads(x) for x in p.read_text().splitlines()]
    job = next(r for r in rows[1:] if r["field_jobs"])
    job["field_jobs"] = [*job["field_jobs"], "co2_ppm"]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    with pytest.raises(SystemExit, match="not in the field list"):
        run(path, "preflight", "--stage", "draft", "--set", "P")


#: an air-exchange-rate field's term list as the v2 field binding prints it (generic vocabulary)
V2_AIR_EXCHANGE_TERMS = ["air exchange rate", "air exchange", "air change", "air changes", "outdoor air exchange",
                         "natural ventilation", "mechanical ventilation", "ventilation rate", "λa", "λ_a", "λ a",
                         "lambda_a", "lambda_{a}", "ach"]


def test_abbreviation_terms_are_a_hard_reject(tmp_path: Path) -> None:
    """An abbreviation- or symbol-like term in the question is rejected whatever the
    share; definition words alone are accepted; the share threshold is >= 0.5."""
    spec = fl.FieldSpec("air_exchange_rate_s", "1/s",
                        "outdoor-air exchange rate of the room (air changes per unit time from ventilation only)",
                        tuple(V2_AIR_EXCHANGE_TERMS))
    assert [t for t in V2_AIR_EXCHANGE_TERMS if fl.abbreviation_like(t)] == \
        ["λa", "λ_a", "λ a", "lambda_a", "lambda_{a}", "ach"]
    for t in ("ACH", "qb", "HEPA", "c0", "g =", "ρa", "r̄", "15-minute"):
        assert fl.abbreviation_like(t), t
    for t in ("people", "occupants", "air change", "ventilation rate"):
        assert not fl.abbreviation_like(t), t
    rows = [g.parse_chunk(r, "t") for r in sup.chunk_rows(
        "d1", "Room study. " + sup.FILLER + "The ventilation gave 1.5 air changes per hour in summer. " + sup.FILLER,
        "PMC101")]
    c = g.build_corpus(rows, "doc_id")
    idx = ck.PaperIndex(c, 200)
    ch = next(x for x in rows if "1.5 air changes" in x.content)
    base = {"quote": "gave 1.5 air changes per hour", "quantity": "air changes", "value": "1.5 air changes per hour",
            "symbol": "", "case": "", "binding_terms": list(spec.terms)}

    def l1(q: str) -> dict[str, Any]:
        return ck.layer1({**base, "question": q}, None, stratum="S3", paper="d1", source_chunk=ch, idx=idx,
                         cfg={}, field=spec)

    for q in ("What was the ACH in the classroom?", "What was lambda_{a} in the classroom?",
              "What was λ a for the room?",
              # the usual way to introduce an abbreviation: in brackets after the words; the
              # share alone (1 of 3 own words) would not reject it
              "What was the air exchange rate (ACH) of the classroom in the winter?",
              "What was the air changes per hour [ACH] of the classroom?"):
        r = l1(q)
        assert "term_abbreviation" in r["reasons"], (q, r)
    assert fl.abbreviation_in_question("What was the rate (ACH)?", V2_AIR_EXCHANGE_TERMS) == ["ach"]
    ok = l1("What was the air changes per hour of the room?")
    assert ok["verdict"] == "pass" and ok["term_leak"] == 0.0, ok
    # a token, not a substring: "teach" does not contain the term "ach"
    assert fl.abbreviation_in_question("Did the teacher teach?", ["ach"]) == []
    # >= 0.5 rejects: one term word against one other content word
    r = ck.layer1({**base, "question": "What was the natural rate?"}, None, stratum="S3",
                  paper="d1", source_chunk=ch, idx=idx, cfg={}, field=fl.FieldSpec(
                      "x", "1/s", "air changes per unit time", ("natural ventilation",)))
    assert r["term_leak"] == 0.5 and "term_leak" in r["reasons"]


def test_known_field_list_sha_sets_expect_in_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A field list whose sha is known (v2) carries its expected in_set split by default:
    leaving expect_in_set out, or giving no held-set records, cannot skip the check."""
    path = write_fields(tmp_path / "f.json")
    sha = g.file_sha(path)
    monkeypatch.setitem(fl.KNOWN_FIELD_LISTS, sha, {"true": 3, "false": 5})
    fc = {**fl.FIELD_DEFAULTS, "path": str(path), "constants": []}
    with pytest.raises(SystemExit, match="expect_in_set"):
        fl.resolve_fields(fc)                                         # no records: in_set unknown -> refused
    held = tmp_path / "held.jsonl"
    held.write_text("".join(json.dumps({"field": f["field"]}) + "\n" for f in FIELDS[:3]))
    res = fl.resolve_fields(fc, str(held))
    assert res["in_set"]["counts"]["true"] == 3 and res["in_set"]["expected"] == {"true": 3, "false": 5}
    assert "1c3778918c76ae206de0fb1b66e256da33faebf8840d6c49ca232552b2911dfa" in fl.KNOWN_FIELD_LISTS


def test_constant_cap_holds_across_cells_and_realised_is_counted(tmp_path: Path) -> None:
    """A constant counts at most constant_cap in a cell's capacity, and at most
    constant_cap over the whole set: a later cell assigned jobs it cannot fill
    records realised < assigned."""
    fs, fc = _specs(tmp_path)
    rows = _rows({("a", "S3"): {"air_density_kg_m3": 5}, ("b", "S3"): {"air_density_kg_m3": 5}})
    plan, jobs = fl.plan_strata(fs, fc, rows, fl.cell_targets(["a", "b"], {"S1": 0, "S2": 0, "S3": 6}), "seed")
    cells = [plan["cells"][p]["S3"] for p in ("a", "b")]
    assert [c["assigned"] for c in cells] == [2, 2]
    assert sorted(c["realised"] for c in cells) == [0, 2] and plan["fields"]["air_density_kg_m3"]["realised"] == 2
    assert sum(len(v) for v in jobs.values()) == 2 == plan["strata"]["S3"]["realised"]
    assert fl.abbreviation_like("ASHRAE") and not fl.abbreviation_like("Health")   # mostly upper case, <= 6
