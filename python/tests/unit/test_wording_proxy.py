"""The wording proxy (glossary benchmark round 2, harness item 2e).

Synthetic cases written from the spec, plus the REAL renderer's output
(:meth:`Glossary.for_chunk`) so the line splitter cannot drift from it.
Offline: no store, no endpoint.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from ragstack.ingestion.glossary_file import build_row, write_glossary_file
from ragstack.ingestion.symbol_glossary import (
    DEFAULT_CONFIG,
    Definition,
    Glossary,
    GlossaryEntry,
    SymbolGlossaryAttacher,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "eval"))
import wording_proxy as wp  # noqa: E402


def _naive_substring(definition: str, term: str) -> bool:
    """The rejected alternative: ordered substring of the normalised strings."""
    return wp.norm_definition(term) in wp.norm_definition(definition)


# --- the renderer's line format -------------------------------------------------


def test_the_splitter_matches_the_real_renderer() -> None:
    g = Glossary((
        GlossaryEntry("Qb", (Definition("breathing flow rate", 0),
                             Definition("exhaled volume — per breath", 50))),
        GlossaryEntry("λv", (Definition("viral deactivation rate", 5),)),
        GlossaryEntry("Zq", (Definition("Fade   Rate of the signal", 9),)),
    ), "llm/2:m:h")
    chunk = "We set Qb = 0.5, λv = 0.3 and Zq = 2 here."
    field = g.for_chunk(chunk, DEFAULT_CONFIG)          # not positional: both Qb definitions
    assert wp.SEPARATOR == " — " and wp.SEPARATOR in field
    entries, bad = wp.parse_field(field)
    assert bad == 0
    by = {e.symbol: e for e in entries}
    assert set(by) == {"qb", "λv", "zq"}
    # the first separator splits; a dash inside a definition stays in the definition
    assert by["qb"].definition == "breathing flow rate; exhaled volume — per breath"
    assert by["zq"].definition == "fade rate of the signal"
    assert {"breath", "flow", "rate", "exhal", "volume", "per"} <= by["qb"].words
    # the line is accepted only with the em dash the renderer writes
    assert wp.parse_field("Qb - breathing rate\nQb – breathing rate") == ([], 2)


def test_record_passes_against_the_real_renderer_field() -> None:
    g = Glossary((GlossaryEntry("λv", (Definition("rate of ventilation of the room", 0),)),),
                 "llm/2:m:h")
    field = g.for_chunk("Here λv = 3 per hour.", DEFAULT_CONFIG)
    assert wp.judge([("c1", field)], ["ventilation rate"])["pass"] is True
    assert wp.judge([("c1", g.for_chunk("No symbol here.", DEFAULT_CONFIG))],
                    ["ventilation rate"])["pass"] is False


# --- normalisation, tokens, stemming -------------------------------------------------


@pytest.mark.parametrize(("token", "stemmed"), [
    ("ventilations", "ventil"), ("ventilation", "ventil"), ("ventilated", "ventil"),
    ("emissions", "emiss"), ("emission", "emiss"), ("breathing", "breath"),
    ("measured", "measur"), ("rates", "rate"), ("rate", "rate"), ("classes", "class"),
    ("was", "was"), ("ions", "ions"), ("sing", "sing"), ("bed", "bed"), ("cases", "case"),
    ("flows", "flow"), ("gas", "gas"),
])
def test_stemming(token: str, stemmed: str) -> None:
    assert wp.stem(token) == stemmed


def test_words_tokenisation_and_normalisation() -> None:
    assert wp.words("The  ﬁltration RATE, a x 2 m3/h") == {"the", "filtr", "rate", "m3"}
    assert wp.words("ＦＵＬＬＷＩＤＴＨ Rate") == {"fullwidth", "rate"}     # NFKC
    assert wp.words("STRASSE straße") == {"strasse"}                       # casefold
    assert wp.words("λ μ") == frozenset()                                  # not [a-z0-9]
    assert wp.norm_symbol(" λ v ") == "λv" and wp.norm_symbol("ＱＢ") == "qb"


# --- containment, not substring, not order ---------------------------------------------


def test_containment_differs_from_substring_both_ways() -> None:
    # order: the term's words are all there, in another order
    d, t = "Qb — rate of ventilation", "ventilation rate"
    assert wp.judge([("c", d)], [t])["pass"] is True
    assert not _naive_substring(d.split(" — ")[1], t)
    # substring: 'rate' is inside 'separated' but is not a word of it
    d, t = "Qb — the separated fraction", "rate"
    assert wp.judge([("c", d)], [t])["pass"] is False
    assert _naive_substring(d.split(" — ")[1], t)
    # stemming makes inflections meet
    assert wp.judge([("c", "Qb — the rates of breathing")], ["breathing rate"])["pass"] is True


def test_any_entry_any_gold_chunk_one_term() -> None:
    f1 = "Ab — inflow of air\nCd — the room volume"
    f2 = "Ef — exhaled particle count"
    # one term's words must sit in ONE definition; they do not combine across entries
    assert wp.judge([("c1", f1)], ["air volume"])["pass"] is False
    # any gold chunk, any entry, any one term
    j = wp.judge([("c1", f1), ("c2", f2)], ["breathing rate", "particle count"])
    assert j["pass"] is True and j["match"] == {"chunk_id": "c2", "symbol": "ef",
                                                "term": "particle count"}
    assert j["gold_fields_nonempty"] == 2


def test_empty_terms_and_empty_fields_never_pass() -> None:
    j = wp.judge([("c", "Qb — λ")], ["λ", "x"])        # no word in either term
    assert j["pass"] is False and j["empty_terms"] == 2
    j = wp.judge([("c", "")], ["rate"])
    assert j["pass"] is False and j["gold_fields_nonempty"] == 0


def test_score_groups_by_quantity_and_stratum() -> None:
    recs = [
        {"record_id": "r1", "gold_chunks": ["c1"], "binding_terms": ["flow rate"],
         "quantity": "Flow  rate", "stratum": "S1"},
        {"record_id": "r2", "gold_chunks": ["c2"], "binding_terms": ["flow rate"],
         "quantity": "flow rate", "stratum": "S1"},
        {"record_id": "r3", "gold_chunks": ["c3"], "binding_terms": ["λ"],
         "quantity": "decay", "stratum": "S3"},
    ]
    arms = {"a": {"c1": "Qb — the flow rate", "c2": "", "c3": "Zq — decay"},
            "none": {"c1": "", "c2": "", "c3": ""}}
    s = wp.score(recs, arms)
    assert s["totals"]["a"]["n"] == 3 and s["totals"]["a"]["pass"] == 1
    assert s["totals"]["a"]["rate"] == round(1 / 3, 4)
    assert s["totals"]["a"]["records_without_usable_term"] == 1
    assert s["by_quantity"]["a"]["flow rate"] == {"n": 2, "pass": 1, "records_with_field": 1}
    assert s["by_stratum"]["a"]["S3"]["pass"] == 0
    assert s["totals"]["none"]["pass"] == 0
    assert s["per_record"]["r1"]["a"]["match"]["symbol"] == "qb"


# --- the CLI ------------------------------------------------------------------------------

PAD = " ".join(["plain filler words keep these chunks apart"] * 4)
TEXT = ("We use Kv, where Kv is the valve flow coefficient, and Pd, where Pd is the pressure "
        "drop.\n" + PAD + "\nTable 1: Kv = 0.8 here.\n" + PAD + "\nAnd Pd = 12 there.\n" + PAD)


def _chunks(doc_id: str, text: str, n: int = 4) -> list[dict[str, Any]]:
    step = len(text) // n + 1
    return [{"chunk_id": f"{doc_id}-{i}", "doc_id": doc_id, "content": text[s:s + step],
             "start_char": s, "end_char": min(s + step, len(text))}
            for i, s in enumerate(range(0, len(text), step))]


@pytest.fixture
def world(tmp_path: Path) -> dict[str, Path]:
    chunks = _chunks("d0", TEXT)
    kv = next(c["chunk_id"] for c in chunks if "Kv = 0.8" in c["content"])
    pd = next(c["chunk_id"] for c in chunks if "Pd = 12" in c["content"])
    assert kv != pd
    (tmp_path / "chunks.jsonl").write_text("".join(json.dumps(c) + "\n" for c in chunks))
    write_glossary_file(tmp_path / "g.jsonl", [build_row("d0", TEXT, SymbolGlossaryAttacher())],
                        producer="synthetic rules")
    recs = [{"record_id": "q1", "gold_chunks": [kv], "binding_terms": ["flow coefficient"],
             "quantity": "valve flow coefficient", "stratum": "S1"},
            {"record_id": "q2", "gold_chunks": [pd], "binding_terms": ["pressure drop"],
             "quantity": "pressure drop", "stratum": "S1"}]
    (tmp_path / "records.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    (tmp_path / "empty.fields.jsonl").write_text(
        "".join(json.dumps({"chunk_id": c["chunk_id"], "symbol_glossary": ""}) + "\n" for c in chunks))
    return {"dir": tmp_path, "chunks": tmp_path / "chunks.jsonl", "g": tmp_path / "g.jsonl",
            "records": tmp_path / "records.jsonl", "empty": tmp_path / "empty.fields.jsonl"}


def test_cli_end_to_end(world: dict[str, Path]) -> None:
    out = world["dir"] / "res" / "proxy.json"
    rc = wp.main(["--records", str(world["records"]), "--chunks", str(world["chunks"]),
                  "--glossary-file", f"rules={world['g']}", "--glossary-file", f"shuf={world['g']}",
                  "--fields", f"none={world['empty']}", "--shuffle-rendered", "shuf=261101|shuf",
                  "--out", str(out)])
    assert rc == 0
    res = json.loads(out.read_text())
    assert res["schema"] == wp.SCHEMA and res["rules"] == wp.RULES
    assert res["totals"]["rules"]["pass"] == 2 and res["totals"]["none"]["pass"] == 0
    # two populated chunks with distinct strings swap: both records lose their words
    assert res["arms"]["shuf"]["shuffle"]["populated"] == 2
    assert res["arms"]["shuf"]["shuffle"]["fixed_points"] == 0
    assert res["totals"]["shuf"]["pass"] == 0
    assert res["totals"]["shuf"]["records_with_field"] == 2
    assert res["arms"]["rules"]["producer"] == "synthetic rules"
    assert res["arms"]["rules"]["documents"]["verified"] == 1
    assert len(res["inputs"]["records"]["sha256"]) == 64 and "commit" in res["provenance"]


def test_cli_refusals(world: dict[str, Path], tmp_path: Path) -> None:
    base = ["--records", str(world["records"])]
    out = ["--out", str(tmp_path / "o.json")]
    # a gold chunk the arm does not have
    (tmp_path / "few.jsonl").write_text(json.dumps({"chunk_id": "x", "symbol_glossary": ""}) + "\n")
    assert wp.main([*base, "--fields", f"a={tmp_path / 'few.jsonl'}", *out]) == wp.EXIT_INPUT
    # a glossary file without the chunks, and an unknown shuffled arm
    assert wp.main([*base, "--glossary-file", f"a={world['g']}", *out]) == wp.EXIT_INPUT
    assert wp.main([*base, "--fields", f"a={world['empty']}", "--shuffle-rendered", "b=1",
                    *out]) == wp.EXIT_INPUT
    # a document whose text is not the file's
    other = [dict(c, content=c["content"].replace("0.8", "0.9")) for c in _chunks("d0", TEXT)]
    (tmp_path / "other.jsonl").write_text("".join(json.dumps(c) + "\n" for c in other))
    assert wp.main([*base, "--chunks", str(tmp_path / "other.jsonl"),
                    "--glossary-file", f"a={world['g']}", *out]) == wp.EXIT_INPUT
    # results inside the repository
    assert wp.main([*base, "--fields", f"a={world['empty']}",
                    "--out", str(ROOT / "proxy.json")]) == wp.EXIT_INPUT
    assert not (ROOT / "proxy.json").exists()
    # a malformed record
    (tmp_path / "bad.jsonl").write_text(json.dumps({"record_id": "r", "gold_chunks": []}) + "\n")
    assert wp.main(["--records", str(tmp_path / "bad.jsonl"), "--fields",
                    f"a={world['empty']}", *out]) == wp.EXIT_INPUT
