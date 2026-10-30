"""Round 3: the drafter on the round-3 pool (strata V / M_num).

* M_num chunks are drafted with the paper's symbols they mention (``sigma_tokens``,
  from the paper's own value statements — no glossary) and answer
  ``symbol_is_the_quantity`` (recorded, never a reject);
* the drafter stays blind: no glossary, no arm, no rendered mention field reaches
  a prompt;
* layer 1 on M_num: no value statement needed, a printed number is; the symbol is
  optional; a record keeps its chunk's stratum;
* set P in fields mode on both strata, its pilot, and the partner export / verdict
  import unchanged.

Fake gateway and fake CLI; synthetic corpora; no store, no network, no model.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.eval import test_glossbench_r2_fields as r2f
from tests.eval import test_glossbench_r3_pool as r3p

_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
if str(_EVAL) not in sys.path:
    sys.path.insert(0, str(_EVAL))

import _glossbench_r2 as g  # noqa: E402
import glossbench_r2_checks as ck  # noqa: E402
import glossbench_r2_draft as dr  # noqa: E402
import glossbench_r2_fields as fl  # noqa: E402
import glossbench_r3_pool as pool  # noqa: E402

from ragstack.ingestion.glossary_file import read_glossary_file  # noqa: E402

PROV = {"commit": "d" * 40, "dirty": False, "untracked": 0}
SERVED = "served-drafter-r3"
#: sha256 of round 2's prompts on main f408b7a: round 3 must not change them
R2_PROMPT_SHA = {
    "DRAFT_SYSTEM": "186ad65b1cd633cd14fc216dd81f1fb631cf94c720fe28012a35a1f02d321b01",
    "DRAFT_SYSTEM_FIELDS": "613d9284d6f3257af8402f4397f8de679c72367d14d79dc1b2710fd04052e52f",
    "VERIFY_SYSTEM": "ce9b606820bddbc89f2e761a73bacafcd6182f426e727bd68dc9396a316afd33",
}

# ------------------------------------------------------------------ the open-mode drafter (set O)

#: per DENSE segment (test_glossbench_r3_pool): question, value, symbol, symbol_is_the_quantity
ANSWERS = {
    "We measured Qb": ("How fast did the boats drift in calm water?", r"0\.\d5 m/s", "Qb", None),
    "Later Kz": ("Which setting was fixed for every run afterwards?", r"3\d", "Kz", None),
    "At dusk Qb": ("How fast did the boats drift at dusk?", r"0\.7\d", "Qb", None),
    "Qb rose in": ("How many attempts showed a rise in boat speed?", "12", "Qb", False),
    "Kz stayed near": ("What level did the setting hold?", "4 units", "Kz", True),
    "Qb fell in": ("In how many attempts did the speed drop?", "3", "", False),
    "In 5 cases Kz": ("In how many situations was the setting larger?", "5", "Kz", False),
    "Qb doubled": ("How often did the speed grow twofold?", "2 times", "Qb", False),
    "Kz changed": ("How often did the setting shift?", "7 times", "Kz", False),
    "Qb was low on": ("On which day was the speed small?", "9", "Qb", False),
}


def fake_open(messages: list[dict[str, str]]) -> dict[str, Any]:
    system, user = messages[0]["content"], messages[-1]["content"]
    body = user.split("<<<\n", 1)[1].split("\n>>>", 1)[0]
    for key, (q, val, sym, sb) in ANSWERS.items():
        if body.startswith(key):
            v = re.search(val, body).group(0)  # type: ignore[union-attr]
            out = {"question": q, "quote": body.strip(), "quantity": "a quantity", "value": v,
                   "binding_terms": ["boats"], "symbol": sym}
            if ck.SYMBOL_BOUND_KEY in system:
                out[ck.SYMBOL_BOUND_KEY] = sb
            return out
    return {"none": True}


class Gateway:
    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.bodies: list[dict[str, Any]] = []

    async def post(self, url: str, json: dict[str, Any]) -> r2f.Resp:  # noqa: A002
        self.bodies.append(json)
        await asyncio.sleep(0)
        content = __import__("json").dumps(self.answer(json["messages"]))
        return r2f.Resp({"model": SERVED, "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 50, "completion_tokens": 20}})

    async def aclose(self) -> None:
        pass


def run(path: Path, *args: str, gw: Any = None, cli: Any = None) -> int:
    return dr.main(["--private-config", str(path), *args], provenance=lambda: PROV,
                   http_factory=(lambda: gw) if gw else None, cli_runner=cli)


def chain(path: Path, set_: str, answer: Any, *extra: str) -> Gateway:
    gw = Gateway(answer)
    assert run(path, "preflight", "--stage", "draft", "--set", set_, *extra) == 0
    assert run(path, "draft", "--set", set_, "--notice-acknowledged", *extra, gw=gw) == 0
    assert run(path, "check", "--set", set_, *extra) == 0
    assert run(path, "preflight", "--stage", "verify", "--set", set_, *extra) == 0
    assert run(path, "verify", "--set", set_, "--notice-acknowledged", *extra, cli=r2f.OkCLI()) == 0
    assert run(path, "summary", "--set", set_, *extra) == 0
    return gw


def world_o(tmp: Path) -> tuple[Path, dict[str, Any], Path]:
    cfg, data = r3p.world(tmp)
    cfg.update({"drafter": {"base_url": "http://127.0.0.1:18999/v1", "empty_retry_s": 0.0},
                "verifier": {"cwd": str(tmp / "vcwd")}, "limits": {"rate_backoffs_s": [0.0, 0.0, 0.0]}})
    path = tmp / "config.json"
    path.write_text(json.dumps(cfg))
    pool.build(cfg, "O", g.out_dir(cfg), provenance=lambda: PROV)
    return path, cfg, data


def jsonl(p: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def test_round3_open_chain_on_v_and_m_num(tmp_path: Path) -> None:
    path, cfg, data = world_o(tmp_path)
    gw = chain(path, "O", fake_open)
    # blindness: no glossary word, no arm, no attachment word, no rendered field of either arm
    gm = read_glossary_file(data / "mention.jsonl")
    corpus = g.build_corpus(g.read_chunks(cfg["corpora"]["O"]["chunks"]), "doc_id")
    fields = {gm.rows[c.doc_id].stamped().for_chunk(c.content, gm.config, start_char=c.start_char)
              for c in corpus.chunks.values()} - {""}
    assert fields and all("—" in f for f in fields)
    lines = {ln for f in fields for ln in f.split("\n")}
    for b in gw.bodies:
        t = json.dumps(b["messages"], ensure_ascii=False)
        assert not any(ln in t for ln in lines)
        low = t.casefold()
        for word in ("glossary", "attach", "value arm", "mention arm", "qwen", "rules/3", "boat drift speed"):
            assert word not in low, word
    out = Path(cfg["out_dir"])
    acc = jsonl(out / "accepted-O.jsonl")
    by = {s: [a for a in acc if a["stratum"] == s] for s in ("V", "M_num")}
    # 3 papers x (3 V + 7 M_num) sampled; every answer above passes layers 1-2
    assert len(by["V"]) == 9 and len(by["M_num"]) == 21
    assert all(a["chunk_stratum"] == a["stratum"] and a["words_only"] is False for a in acc)
    assert all(a[ck.SYMBOL_BOUND_KEY] in (True, False) for a in by["M_num"])
    assert all(ck.SYMBOL_BOUND_KEY not in a for a in by["V"])
    assert sum(a[ck.SYMBOL_BOUND_KEY] for a in by["M_num"]) == 3          # "Kz stayed near" on 3 papers
    assert sum(a["symbol"] == "" for a in by["M_num"]) == 3               # a symbol-less M_num record passes
    s = json.loads((out / "acceptance-summary-O.json").read_text())
    assert s["round3"]["symbol_bound_by_stratum"]["M_num"] == {"True": 3, "False": 18}
    # M_num prompts name the paper's symbols the chunk mentions; V prompts the value-stating tokens
    mnum = [b for b in gw.bodies if ck.SYMBOL_BOUND_KEY in b["messages"][0]["content"]]
    assert len(mnum) == 21 and all("symbols the paper uses (" in b["messages"][-1]["content"] for b in mnum)
    assert any("(Kz, Qb)" in b["messages"][-1]["content"] or "(Kz)" in b["messages"][-1]["content"] for b in mnum)
    assert all("Tokens that state a value" in b["messages"][-1]["content"]
               for b in gw.bodies if b not in mnum)



def test_m_num_prompt_unit_and_round2_prompts_unchanged() -> None:
    m = ck.draft_messages("Qb rose in 12 of the trials.", "M_num", [], sigma_tokens=["Qb"])
    assert m[0]["content"] == ck.DRAFT_SYSTEM_MNUM and ck.SYMBOL_BOUND_KEY in m[0]["content"]
    assert "(Qb)" in m[1]["content"] and "glossary" not in json.dumps(m).casefold()
    v = ck.draft_messages("We measured Qb = 0.5 m/s.", "V", ["Qb"])
    s1 = ck.draft_messages("We measured Qb = 0.5 m/s.", "S1", ["Qb"])
    assert v[0]["content"] == s1[0]["content"] == ck.DRAFT_SYSTEM and v[1]["content"] == s1[1]["content"]
    for name, sha in R2_PROMPT_SHA.items():
        assert g.sha_text(getattr(ck, name)) == sha, name
    spec = fl.FieldSpec("f", "m", "a definition", ("t",), "parameter", None, False)
    plain = ck.field_draft_messages("x", spec)
    bound = ck.field_draft_messages("x", spec, symbol_bound=True)
    assert plain[0]["content"] == ck.DRAFT_SYSTEM_FIELDS and plain[1] == bound[1]
    assert bound[0]["content"] == ck.DRAFT_SYSTEM_FIELDS_MNUM and ck.SYMBOL_BOUND_KEY in bound[0]["content"]


def test_parse_symbol_bound() -> None:
    base = {"question": "q", "quote": "x", "quantity": "y", "value": "1", "binding_terms": ["a"], "symbol": ""}
    for raw, want in ((True, True), (False, False), ("yes", True), ("false", False)):
        d, e = ck.parse_draft(json.dumps({**base, ck.SYMBOL_BOUND_KEY: raw}), symbol_bound=True)
        assert e is None and d is not None and d[ck.SYMBOL_BOUND_KEY] is want
    d, e = ck.parse_draft(json.dumps(base), symbol_bound=True)
    assert d is None and ck.SYMBOL_BOUND_KEY in str(e)
    d, e = ck.parse_draft(json.dumps({"none": True}), symbol_bound=True)
    assert d == {"none": True}
    d, _ = ck.parse_draft(json.dumps({**base, ck.SYMBOL_BOUND_KEY: True}))      # round 2: not read
    assert d is not None and ck.SYMBOL_BOUND_KEY not in d


def _l1_world(tmp: Path) -> tuple[g.Corpus, ck.PaperIndex]:
    rows = []
    for i, (doc, kind) in enumerate(r3p.LAYOUT.items(), 1):
        rows += r3p.chunk_rows(doc, r3p.segs(kind, i))[0]
    c = g.build_corpus([g.parse_chunk(r, "t") for r in rows], "doc_id")
    return c, ck.PaperIndex(c, 200)


@pytest.mark.parametrize("change,stratum,verdict,code", [
    ({}, "M_num", "pass", None),
    ({"symbol": ""}, "M_num", "pass", None),                          # M_num: the symbol is optional
    ({"symbol": "Zz"}, "M_num", "reject", "symbol_not_in_chunk"),
    ({"value": "twelve"}, "M_num", "reject", "quantity_not_in_quote"),  # the value must be printed in the quote
    ({"quote": "Qb rose in", "value": "12"}, "M_num", "reject", "quantity_not_in_quote"),  # a number is required
    ({"symbol": ""}, "V", "reject", "symbol_missing"),                # V (open mode) keeps round 2's S1/S2 rule
    ({"symbol": ""}, "S1", "reject", "symbol_missing"),
])
def test_layer1_on_round3_strata(tmp_path: Path, change: dict[str, Any], stratum: str, verdict: str,
                                 code: str | None) -> None:
    c, idx = _l1_world(tmp_path)
    chunk = c.chunks["d1-c3"]                                          # "Qb rose in 12 of the trials."
    draft = {"question": "How many attempts showed a rise in boat speed?", "quote": chunk.content.strip(),
             "quantity": "trials", "value": "12", "binding_terms": ["boats"], "symbol": "Qb",
             ck.SYMBOL_BOUND_KEY: False, **change}
    out = ck.layer1(draft, None, stratum=stratum, paper="d1", source_chunk=chunk, idx=idx, cfg={})
    assert out["verdict"] == verdict, out["reasons"]
    if code:
        assert code in out["reasons"]
    if stratum in ck.R3_STRATA:
        assert out["record_stratum"] == stratum


# ------------------------------------------------------------------ fields mode (set P)

#: a room study with symbols: Qv is defined (the rules glossary holds it); V = s1, s6, s7;
#: M_num = s2, s3, s4, s5, s8, s9, s10
ROOM = [
    "where Qv is the outdoor-air supply of the room. ",
    "In the winter case the outdoor-air supply was Qv = 0.{i}5 m3/s for the room. ",
    "Two infected students and twenty-one susceptible classmates shared room {i} while Qv was low. ",
    "The ventilation with Qv gave {i}.5 air changes per hour in summer. ",
    "We took an air density of 1.2 kg/m3 near the Qv inlet. ",
    "The mouth opening was about 2 cm2 when Qv was measured. ",
    "Later Kz = 3{i} was fixed. ",
    "The summer supply was Qv = 0.{i}9 m3/s at noon. ",
    "Qv doubled in 2 rooms. ",
    "Kz stayed near 4 units. ",
    "Qv fell in 3 runs. ",
]


def world_p(tmp: Path, n_papers: int = 4) -> tuple[Path, dict[str, Any]]:
    from ragstack.ingestion.glossary_file import build_row, rederive_file, write_glossary_file
    from ragstack.ingestion.symbol_glossary import DEFAULT_CONFIG, SymbolGlossaryAttacher

    data = tmp / "data"
    data.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    texts: dict[str, str] = {}
    for i in range(1, n_papers + 1):
        doc = f"d{i}"
        parts = [s.format(i=i) for s in ROOM]
        s = 0
        for n, p in enumerate(parts):
            rows.append({"id": f"{doc}-c{n}", "doc_id": doc, "content": p, "start_char": s, "end_char": s + len(p),
                         "metadata": {"source_path": f"PMC10{i}", "title": f"Room study {i}"}})
            s += len(p)
        texts[doc] = "".join(parts)
    chunks = data / "chunks.jsonl"
    chunks.write_text("".join(json.dumps(r) + "\n" for r in rows))
    att = SymbolGlossaryAttacher(DEFAULT_CONFIG)
    value = data / "value.jsonl"
    write_glossary_file(value, [build_row(d, texts[d], att) for d in sorted(texts)], producer="synthetic",
                        created="2026-10-30T00:00:00Z")
    mention = data / "mention.jsonl"
    rederive_file(value, mention, attach="mention")
    fpath = r2f.write_fields(data / "fields.json")
    cfg = {"out_dir": str(tmp / "out"),
           "corpora": {"P": {"chunks": [str(chunks)], "expected_chunks": len(rows), "paper_key": "doc_id",
                             "arms": {"value": str(value), "mention": str(mention)}, "draft_mode": "fields",
                             "fields": {"path": str(fpath), "min_field_chunks": 2,
                                        "constants": ["gravity_m_s2", "air_density_kg_m3"]}}},
           "pool_r3": {"per_set": {"P": {"n_papers": n_papers, "v_per_paper": 2, "m_per_paper": 4,
                                         "gold_differs_gate": 0, "gold_differs_gate_per_stratum": 0}}},
           "drafter": {"base_url": "http://127.0.0.1:18999/v1", "empty_retry_s": 0.0},
           "verifier": {"cwd": str(tmp / "vcwd")}, "limits": {"rate_backoffs_s": [0.0, 0.0, 0.0]}}
    path = tmp / "config.json"
    path.write_text(json.dumps(cfg))
    pool.build(cfg, "P", g.out_dir(cfg), provenance=lambda: PROV)
    return path, cfg


def fake_fields(messages: list[dict[str, str]]) -> dict[str, Any]:
    d = r2f.fake_field_draft(messages[-1]["content"])
    if not d.get("none") and ck.SYMBOL_BOUND_KEY in messages[0]["content"]:
        d[ck.SYMBOL_BOUND_KEY] = bool(d.get("symbol"))
    return d


def test_fields_mode_pool_plans_both_strata(tmp_path: Path) -> None:
    _, cfg = world_p(tmp_path)
    header, rows = r2f.pool_rows(cfg)
    assert header["schema"] == pool.POOL_SCHEMA and header["draft_mode"] == "fields"
    assert header["field_plan"]["per_paper"] == {"V": 2, "M_num": 4}
    assert set(header["field_plan"]["strata"]) == {"V", "M_num"}
    jobs = [r for r in rows if r["field_jobs"]]
    assert {r["stratum"] for r in jobs} == {"V", "M_num"}
    assert all(r["sampled"] == bool(r["field_jobs"]) for r in rows)
    m = json.loads((Path(cfg["out_dir"]) / "pool-manifest-P.json").read_text())
    assert m["gate"]["gold_differs_sampled"] == sum(r["gold_differs"] for r in rows if r["sampled"])
    assert m["fields"]["jobs"] == sum(len(r["field_jobs"]) for r in rows)


def test_fields_chain_pilot_and_partner_round_trip(tmp_path: Path) -> None:
    path, cfg = world_p(tmp_path)
    out = Path(cfg["out_dir"])
    gw = chain(path, "P", fake_fields)
    acc = jsonl(out / "accepted-P.jsonl")
    # both strata are drafted; which records pass depends on the field each chunk is paired with
    # (since the pilot: any field whose dimension a printed number has, terms ranking)
    assert {d["stratum"] for d in jsonl(out / "draft-P.jsonl")} == {"V", "M_num"}
    assert acc and {a["stratum"] for a in acc} <= {"V", "M_num"} and not any(a["words_only"] for a in acc)
    assert all(ck.SYMBOL_BOUND_KEY in a for a in acc if a["stratum"] == "M_num")
    for b in gw.bodies:                                               # fields mode: no symbol hint, blind
        t = json.dumps(b["messages"]).casefold()
        assert "glossary" not in t and "symbols the paper uses" not in t and "tokens that state" not in t
    exp = tmp_path / "export.jsonl"
    assert run(path, "export-partner", "--set", "P", str(exp)) == 0
    rows = jsonl(exp)
    assert rows and all(set(r) == {"id", "question", "source", "quote", "field", "case", "model",
                                   "prompt_sha256", "value"} for r in rows)
    vp = tmp_path / "verdicts.json"
    vp.write_text(json.dumps({"records": [{"id": r["id"], "verdict": "accept", "basis": "sampled"} for r in rows],
                              "sample": {"n": len(rows), "errors": 0, "seed": "s"}}))
    assert run(path, "import-partner-verdicts", "--set", "P", str(vp)) == 0
    # the pilot: K papers, N jobs, its own files, marked
    chain(path, "P", fake_fields, "--pilot", "6", "--papers", "2")
    drafts = jsonl(out / "draft-P-pilot.jsonl")
    assert 0 < len({(d["chunk_id"], d["field"]) for d in drafts}) <= 6 and len({d["paper"] for d in drafts}) <= 2
    pacc = jsonl(out / "accepted-P-pilot.jsonl")
    assert pacc and all(a["pilot"] is True for a in pacc)
    pexp = tmp_path / "pilot.jsonl"
    assert run(path, "export-partner", "--set", "P", str(pexp), "--pilot", "6", "--papers", "2") == 0
    prow = jsonl(pexp)
    vp2 = tmp_path / "pilot-verdicts.json"
    vp2.write_text(json.dumps({"records": [{"id": r["id"], "verdict": "accept", "basis": "sampled"} for r in prow],
                               "sample": {"n": len(prow), "errors": 0, "seed": "s"}}))
    assert run(path, "import-partner-verdicts", "--set", "P", str(vp2), "--pilot", "6", "--papers", "2") == 0
    res = json.loads((out / "partner-verdicts-P-pilot.json").read_text())
    assert res["pilot"] is True and res["promotion"]["n"] == len(prow)


def test_open_mode_pilot_on_round3(tmp_path: Path) -> None:
    path, cfg, _ = world_o(tmp_path)
    chain(path, "O", fake_open, "--pilot", "8", "--papers", "2")
    drafts = jsonl(Path(cfg["out_dir"]) / "draft-O-pilot.jsonl")
    assert 0 < len({d["chunk_id"] for d in drafts}) <= 8 and len({d["paper"] for d in drafts}) <= 2


def test_round2_field_plan_strata_unchanged() -> None:
    assert fl.strata_order(["S3", "S1", "S2"]) == fl.STRATA
    assert fl.strata_order(["V", "M_num"]) == ("M_num", "V")
    t = fl.cell_targets(["a", "b"], {"S1": 2, "S2": 1, "S3": 1}, 5)
    assert list(t) == [("a", "S1"), ("a", "S2"), ("a", "S3"), ("b", "S1"), ("b", "S2"), ("b", "S3")]
