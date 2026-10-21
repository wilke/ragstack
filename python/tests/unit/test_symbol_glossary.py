"""The per-document symbol glossary (#754 fix A): rules, validation, cache,
attacher. All synthetic text — no fixture copies a paper."""
from __future__ import annotations

import json

import pytest

from ragstack.ingestion.symbol_glossary import (
    EXTRACTOR_VERSION,
    VERSION_KEY,
    Definition,
    Glossary,
    GlossaryConfig,
    GlossaryEntry,
    SymbolGlossaryAttacher,
    SymbolGlossaryCache,
    attacher_from_mode,
    canonical_map,
    canonical_symbol,
    config_from_json,
    content_key,
    extract_glossary,
    is_specific_symbol,
    symbol_occurs,
    validate_entries,
)
from ragstack.models import Chunk, Document

WHERE = (
    "Each person exhales droplets at a rate P = Qb n(r), where Qb is the "
    "breathing flow rate (exhaled volume per time). We introduce a mask factor."
)
APPOSITIVE = "The second equality defines the effective infectious drop radius r¯, given below."
IS_THE = "The quantity λ¯c=λa+λf is the air purification rate of the room."
PAREN = "We estimate the settling speed (vs) from Stokes' law."


# --- rules --------------------------------------------------------------------


def test_where_rule() -> None:
    g = extract_glossary(WHERE)
    assert g.as_dict() == {"Qb": ["breathing flow rate (exhaled volume per time)"]}


def test_where_rule_continuation_clause() -> None:
    text = "Here N = Qb Cq, where Qb is the breathing flow rate, and Cq is the quanta concentration."
    assert extract_glossary(text).as_dict() == {
        "Cq": ["quanta concentration"],
        "Qb": ["breathing flow rate"],
    }


def test_where_rule_with_value_names_the_symbol_not_the_unit() -> None:
    g = extract_glossary("a time τ=Vb/Qb, where Vb≈500 mL is the volume per breath. Next.")
    assert g.as_dict() == {"Vb": ["volume per breath"]}


def test_appositive_rule() -> None:
    assert extract_glossary(APPOSITIVE).as_dict() == {
        canonical_symbol("r¯"): ["effective infectious drop radius"]
    }


def test_is_the_rule_with_equation() -> None:
    assert extract_glossary(IS_THE).as_dict() == {
        canonical_symbol("λ¯c"): ["air purification rate of the room"]
    }


def test_parenthetical_rule() -> None:
    assert extract_glossary(PAREN).as_dict() == {"vs": ["settling speed"]}


def test_appositive_followed_by_a_relation_is_a_comparison_not_a_definition() -> None:
    text = "The sedimentation rate is smaller than the air exchange rate, λs(r)<λa, here."
    assert extract_glossary(text).as_dict() == {}


def test_bare_head_noun_is_not_a_definition() -> None:
    # "at the rate λv": no modifier, says nothing — the λv trap's rules side.
    assert extract_glossary("Virions decay at the rate λv in dry air.").as_dict() == {}


def test_value_is_not_a_definition() -> None:
    assert extract_glossary("where Qb is 0.5 m3/h for a resting adult.").as_dict() == {}


def test_symbol_defined_twice_keeps_both_in_document_order() -> None:
    text = (
        "where λv is the viral deactivation rate. Later, where λv is the decay "
        "constant of the virus. And where λv is the viral deactivation rate."
    )
    g = extract_glossary(text)
    assert g.as_dict() == {"λv": ["viral deactivation rate", "decay constant of the virus"]}


def test_definitions_capped_per_symbol() -> None:
    text = " ".join(f"where Qb is the flow number {w}." for w in ["one", "two", "three", "four"])
    cfg = GlossaryConfig(max_definitions_per_symbol=2, rule_respectively=False)
    assert extract_glossary(text, cfg).as_dict() == {"Qb": ["flow number one", "flow number two"]}


def test_definition_words_capped() -> None:
    text = "where Qb is the a b c d e f g h i j k l m n o p rate."
    (defn,) = extract_glossary(text, GlossaryConfig(max_definition_words=3)).as_dict().get("Qb", [""])
    assert len(defn.split()) <= 3


def test_rule_toggles() -> None:
    cfg = GlossaryConfig(rule_where=False, rule_is_the=False)
    assert extract_glossary(WHERE, cfg).as_dict() == {}
    assert extract_glossary(PAREN, GlossaryConfig(rule_parenthetical=False)).as_dict() == {}


@pytest.mark.parametrize(
    "token,ok",
    [
        ("Qb", True), ("Cq", True), ("λv", True), ("r¯", True), ("v¯s", True),
        ("r0", True), ("fd", True), ("ϕs", True),
        ("r", False), ("V", False), ("The", False), ("In", False), ("is", False),
        ("RH", False), ("CET", False), ("rate", False), ("mL", False), ("m3", False),
        ("μm", False), ("", False), ("1a", False), ("Q b", True),  # a spaced key folds to "Qb"
    ],
)
def test_specificity_guard(token: str, ok: bool) -> None:
    assert is_specific_symbol(token) is ok


def test_single_letter_opt_in() -> None:
    assert is_specific_symbol("V", allow_single_letter=True)
    text = "where V is the room volume."
    assert extract_glossary(text).as_dict() == {}
    assert extract_glossary(text, GlossaryConfig(allow_single_letter=True)).as_dict() == {
        "V": ["room volume"]
    }


def test_deterministic_and_sorted() -> None:
    text = WHERE + " " + APPOSITIVE + " " + PAREN
    a, b = extract_glossary(text), extract_glossary(text)
    assert a == b
    assert [e.symbol for e in a.entries] == sorted(e.symbol for e in a.entries)
    assert a.version == EXTRACTOR_VERSION == "rules/2"


@pytest.mark.parametrize(
    "text",
    ["", "   \n", "no symbols here at all.", "where where where is is the the", "(" * 5000,
     "where Qb is the " + "x" * 100_000, "\x00�" * 100, "where ¯ is the bar."],
)
def test_never_raises(text: str) -> None:
    assert isinstance(extract_glossary(text), Glossary)


def test_definitions_are_document_spans() -> None:
    text = WHERE + " " + APPOSITIVE + " " + IS_THE
    flat = " ".join(text.split())
    for defs in extract_glossary(text).as_dict().values():
        for d in defs:
            assert d in flat


# --- rendering ----------------------------------------------------------------


def _e(sym: str, *defs: str, offsets: tuple[int, ...] = ()) -> GlossaryEntry:
    offs = offsets or tuple(range(len(defs)))
    return GlossaryEntry(canonical_symbol(sym), tuple(Definition(d, o) for d, o in zip(defs, offs, strict=True)))


def test_for_chunk_only_symbols_present_as_tokens() -> None:
    g = Glossary((_e("Qb", "breathing flow rate"), _e("λv", "deactivation rate")))
    assert g.for_chunk("We take Qb=0.5 m3/h.") == "Qb — breathing flow rate"
    assert g.for_chunk("We take Qbnd(r) as the source.") == ""  # inside a longer token
    assert g.for_chunk("λv=0.3/h and Qb=0.5") == "Qb — breathing flow rate\nλv — deactivation rate"
    assert g.for_chunk("") == ""


def test_for_chunk_caps() -> None:
    g = Glossary(tuple(_e(f"Q{c}", "some long definition here") for c in "abcdefgh"))
    chunk = " ".join(f"Q{c}" for c in "abcdefgh")
    cfg = GlossaryConfig(max_entries_per_chunk=3, attach_only_with_value=False)
    assert len(g.for_chunk(chunk, cfg).splitlines()) == 3
    out = g.for_chunk(chunk, GlossaryConfig(max_chars_per_chunk=60, attach_only_with_value=False))
    assert len(out) <= 60 and out


def test_symbol_occurs_is_case_sensitive_and_bounded() -> None:
    assert symbol_occurs("Qb", "x Qb y")
    assert not symbol_occurs("Qb", "x qb y")
    assert not symbol_occurs("Qb", "xQb y")
    assert symbol_occurs("r¯", "r¯=0.5 μm")
    assert not symbol_occurs("r", "r¯=0.5 μm")  # the bar is part of the token


# --- validation (the LLM glossary's gate) -------------------------------------

DOC = (
    "The virions become deactivated at a rate λv that depends on humidity. We "
    "treat the deactivation rate as bounded. The air change rate λa is set by "
    "the ventilation system; where Qb is the breathing flow rate."
)


def test_validate_keeps_document_supported_entries() -> None:
    g, rejected = validate_entries(
        [("λv", "deactivation rate"), ("λa", "air change rate")], DOC, "llm/1:m:h"
    )
    assert g.as_dict() == {"λa": ["air change rate"], "λv": ["deactivation rate"]}
    assert rejected == 0 and g.version == "llm/1:m:h"


def test_validate_rejects_a_lexicon_definition() -> None:
    # The λv trap: "viral" is not in the document, so a model's prior-knowledge
    # phrase cannot pass even though "ventilation" happens to be.
    g, rejected = validate_entries([("λv", "viral ventilation rate")], DOC, "llm/1")
    assert g.as_dict() == {} and rejected == 1


def test_validate_rejects_symbol_not_in_document() -> None:
    g, rejected = validate_entries([("λq", "deactivation rate")], DOC, "llm/1")
    assert g.as_dict() == {} and rejected == 1


def test_validate_strips_function_arguments_and_merges() -> None:
    g, _ = validate_entries([("λv(r)", "deactivation rate"), ("λv", "deactivation rate")], DOC, "v")
    assert g.as_dict() == {"λv": ["deactivation rate"]}


def test_validate_applies_specificity_guard() -> None:
    g, rejected = validate_entries([("V", "change rate")], DOC + " V", "v")
    assert g.as_dict() == {} and rejected == 1


def test_validate_tolerates_junk() -> None:
    g, rejected = validate_entries([("", ""), ("Qb", ""), ("Qb", "0.5")], DOC, "v")
    assert g.as_dict() == {} and rejected == 3


# --- config -------------------------------------------------------------------


def test_config_from_json() -> None:
    cfg = config_from_json('{"max_entries_per_chunk": "4", "allow_single_letter": true, "bogus": 1}')
    assert cfg.max_entries_per_chunk == 4 and cfg.allow_single_letter is True
    assert config_from_json("") == GlossaryConfig()
    assert config_from_json("not json") == GlossaryConfig()
    assert config_from_json('{"rule_where": "yes"}').rule_where is True  # bad bool ignored


# --- cache --------------------------------------------------------------------


def test_cache_roundtrip_last_wins_and_version_pin(tmp_path) -> None:
    path = tmp_path / "cache.jsonl"
    cache = SymbolGlossaryCache(path, writable=True)
    key = content_key(DOC)
    cache.put(key, Glossary((_e("λv", "deactivation rate", offsets=(7,)),), "llm/1:a:1"))
    cache.put(key, Glossary((_e("λa", "air change rate", offsets=(9,)),), "llm/1:b:2"))
    cache.close()
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("{corrupt\n")
    ro = SymbolGlossaryCache(path)
    assert ro.get(key) == ("llm/1:b:2", [("λa", (("air change rate", 9),))])
    assert ro.get(key, "llm/1:a:1") == ("llm/1:a:1", [("λv", (("deactivation rate", 7),))])
    assert ro.get(key, "nope") is None and ro.get("missing") is None
    with pytest.raises(RuntimeError):
        ro.put(key, Glossary())
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["k"] == key


def test_content_key_ignores_outer_whitespace() -> None:
    assert content_key("  abc \n") == content_key("abc") != content_key("abd")


# --- attacher -----------------------------------------------------------------


def _doc_and_chunks(text: str) -> tuple[Document, list[Chunk]]:
    doc = Document(id="d1", content=text)
    mid = len(text) // 2
    return doc, [
        Chunk(id="c1", doc_id="d1", content=text[:mid], start_char=0, end_char=mid),
        Chunk(id="c2", doc_id="d1", content=text[mid:], start_char=mid, end_char=len(text)),
    ]


def test_attacher_rules_carries_definition_to_the_value_chunk() -> None:
    text = WHERE + " " + "filler " * 40 + "Table 1: Qb=0.5 m3/h."
    doc, chunks = _doc_and_chunks(text)
    result = SymbolGlossaryAttacher().apply([doc], chunks)
    assert "Qb — breathing flow rate" in chunks[1].symbol_glossary
    assert "Qb" not in chunks[1].content.split("Qb=")[0]  # the value chunk never said it
    assert all(c.metadata[VERSION_KEY] == "rules/2" for c in chunks)
    assert result.docs_rules == 1 and result.docs_cached == 0
    # Value-only (the default): the defining chunk mentions Qb but states no
    # value, so only the caption chunk carries the glossary.
    assert result.chunks_with_glossary == 1 and chunks[0].symbol_glossary == ""
    doc, chunks = _doc_and_chunks(text)
    SymbolGlossaryAttacher(GlossaryConfig(attach_only_with_value=False)).apply([doc], chunks)
    assert all("Qb — breathing flow rate" in c.symbol_glossary for c in chunks)


def test_attacher_symbol_free_chunk_gets_empty_but_is_stamped() -> None:
    doc, chunks = _doc_and_chunks("plain prose " * 30)
    SymbolGlossaryAttacher().apply([doc], chunks)
    assert [c.symbol_glossary for c in chunks] == ["", ""]
    assert all(c.metadata[VERSION_KEY] == "rules/2" for c in chunks)


def test_attacher_prefers_cache_and_revalidates(tmp_path) -> None:
    path = tmp_path / "c.jsonl"
    cache = SymbolGlossaryCache(path, writable=True)
    # One supported entry and one a laxer validator might have let in.
    cache.put(content_key(DOC), Glossary(
        (_e("λv", "deactivation rate", "viral ventilation rate"),), "llm/1:m:h"))
    cache.close()
    doc, chunks = _doc_and_chunks(DOC)
    att = SymbolGlossaryAttacher(GlossaryConfig(attach_only_with_value=False),
                                 cache=SymbolGlossaryCache(path))
    result = att.apply([doc], chunks)
    assert chunks[0].symbol_glossary == "λv — deactivation rate"
    assert chunks[0].metadata[VERSION_KEY] == "llm/1:m:h"
    assert result.docs_cached == 1 and result.docs_rules == 0


def test_attacher_falls_back_to_rules_on_cache_miss(tmp_path) -> None:
    att = SymbolGlossaryAttacher(cache=SymbolGlossaryCache(tmp_path / "empty.jsonl"))
    doc, chunks = _doc_and_chunks(WHERE)
    att.apply([doc], chunks)
    assert chunks[0].metadata[VERSION_KEY] == "rules/2"


def test_attacher_from_mode(tmp_path) -> None:
    assert attacher_from_mode("off") is None
    att = attacher_from_mode("rules", '{"max_entries_per_chunk": 2}')
    assert att is not None and att.config.max_entries_per_chunk == 2 and att.cache is None
    att = attacher_from_mode("rules", "", str(tmp_path / "missing.jsonl"))
    assert att is not None and att.cache is not None and len(att.cache) == 0
    with pytest.raises(ValueError):
        attacher_from_mode("llm")
    prov = attacher_from_mode("rules").provenance()  # type: ignore[union-attr]
    assert prov["symbol_glossary"]["extractor"] == "rules/2"


def test_chunk_dump_omits_empty_glossary() -> None:
    c = Chunk(id="c", doc_id="d", content="x")
    assert "symbol_glossary" not in c.model_dump()
    c.symbol_glossary = "Qb — breathing flow rate"
    assert c.model_dump()["symbol_glossary"] == "Qb — breathing flow rate"
    assert Chunk.model_validate(c.model_dump()).symbol_glossary == c.symbol_glossary


# --- canonical symbol keys (one function at extraction and at matching) -------


@pytest.mark.parametrize(
    "a,b",
    [
        ("r¯", "r\u0304"),          # spacing vs combining macron
        ("r\u02c9", "r\u0304"),     # modifier-letter macron
        ("r\u0305", "r\u0304"),     # combining overline
        ("µm", "μm"),               # micro sign vs Greek mu
        ("λ_v", "λv"),
        ("λ_{v}", "λv"),
        ("λ v", "λv"),
        ("\\lambda_v", "λv"),
        ("\\lambda_{v}", "λv"),
        ("Q_b", "Qb"),
        ("ϕs", "φs"),
        ("ϵ", "ε"),
        ("𝐷𝑘", "Dk"),               # math italic
        ("ā", "a\u0304"),           # precomposed vs decomposed
    ],
)
def test_canonical_symbol_variants(a: str, b: str) -> None:
    assert canonical_symbol(a) == canonical_symbol(b)


@pytest.mark.parametrize(
    "symbol,text",
    [("r¯", "r\u0304 = 0.5 μm"), ("r\u0304", "a r¯=0.5 μm"), ("λv", "λ_v = 0.3/h"),
     ("λv", "\\lambda_{v} = 0.3/h"), ("λ_v", "λv=0.3/h"), ("φs", "ϕs ≈ 1%")],
)
def test_symbol_matches_across_spellings(symbol: str, text: str) -> None:
    assert symbol_occurs(symbol, text)


def test_barred_symbol_never_attaches_to_bare_letter() -> None:
    g = Glossary((_e("r¯", "effective infectious drop radius"),))
    assert g.for_chunk("the radius r of a drop") == ""
    assert g.for_chunk("with r\u0304 = 0.5 μm") == f"{canonical_symbol('r¯')} — effective infectious drop radius"


def test_rules_unify_spellings_into_one_key() -> None:
    text = "where λ_v is the viral deactivation rate. Later λv=0.3/h is used."
    g = extract_glossary(text)
    assert g.as_dict() == {"λv": ["viral deactivation rate"]}
    assert g.for_chunk("λv=0.3/h") == "λv — viral deactivation rate"


@pytest.mark.parametrize("unit", ["m", "h", "μm", "µm", "m3", "mL", "m³", "kPa"])
def test_units_are_not_symbols(unit: str) -> None:
    assert not is_specific_symbol(unit)


def test_canonical_map_offsets_point_into_raw_text() -> None:
    raw = "aµ r¯ \\lambda_{v} x"
    canon, pos = canonical_map(raw)
    assert len(pos) == len(canon) + 1 and pos[-1] == len(raw)
    i = canon.index("λv")
    assert raw[pos[i]:].startswith("\\lambda")
    assert raw[pos[canon.index("x")]] == "x"


def test_rendering_is_nfc() -> None:
    g = Glossary((_e("Qb", "de\u0301bit d'air"),))  # decomposed é
    assert g.for_chunk("Qb=1") == "Qb — d\u00e9bit d'air"


# --- positional definitions ---------------------------------------------------


def test_positional_definition_nearest_before_chunk_start() -> None:
    g = Glossary((_e("λv", "first meaning", "second meaning", offsets=(100, 5000)),))
    assert g.entries[0].ambiguous
    m = GlossaryConfig(attach_only_with_value=False)  # these chunks mention λv, no value
    assert g.for_chunk("λv here", m, start_char=50) == "λv — first meaning"    # none before: first after
    assert g.for_chunk("λv here", m, start_char=100) == "λv — first meaning"   # at
    assert g.for_chunk("λv here", m, start_char=4999) == "λv — first meaning"
    assert g.for_chunk("λv here", m, start_char=6000) == "λv — second meaning"
    assert g.for_chunk("λv here", m) == "λv — first meaning; second meaning"   # no position: all
    assert g.for_chunk("λv", GlossaryConfig(positional=False, attach_only_with_value=False),
                       start_char=6000) == "λv — first meaning; second meaning"
    assert g.for_chunk("λv = 0.3/h", start_char=6000) == "λv — second meaning"  # default: value


def test_rules_record_raw_offsets_and_flag_reuse() -> None:
    text = "where Qb is the breathing flow rate. " + "x " * 200 + "where Qb is the quanta budget."
    g = extract_glossary(text)
    (entry,) = g.entries
    assert [d.text for d in entry.definitions] == ["breathing flow rate", "quanta budget"]
    assert [text[d.offset:d.offset + 2] for d in entry.definitions] == ["Qb", "Qb"]
    assert g.ambiguous_symbols == ["Qb"]


def test_attacher_uses_chunk_position() -> None:
    first = "where Qb is the breathing flow rate. Qb = 1. "
    text = first + "pad " * 50 + "where Qb is the quanta budget. " + "pad " * 50 + "Qb=0.5."
    doc = Document(id="d", content=text)
    cut = len(first) + 100
    chunks = [Chunk(id="a", doc_id="d", content=text[:cut], start_char=0, end_char=cut),
              Chunk(id="b", doc_id="d", content=text[cut:], start_char=cut, end_char=len(text))]
    result = SymbolGlossaryAttacher().apply([doc], chunks)
    assert chunks[0].symbol_glossary == "Qb — breathing flow rate"
    assert chunks[1].symbol_glossary == "Qb — breathing flow rate"  # 2nd def is inside b, after its start
    late = Chunk(id="c", doc_id="d", content="Qb=0.5.", start_char=len(text) - 7, end_char=len(text))
    SymbolGlossaryAttacher().apply([doc], [late])
    assert late.symbol_glossary == "Qb — quanta budget"
    assert result.ambiguous_symbols == 1


# --- "respectively" -----------------------------------------------------------


def test_respectively_rule_pairs_lists() -> None:
    text = ("Here Qb, Cq and λv are the breathing flow rate, the quanta concentration "
            "and the deactivation rate, respectively.")
    assert extract_glossary(text).as_dict() == {
        "Cq": ["quanta concentration"], "Qb": ["breathing flow rate"], "λv": ["deactivation rate"]}


def test_respectively_rule_refuses_count_mismatch() -> None:
    text = "Qb and Cq are the breathing flow rate, x and the quanta concentration, respectively."
    assert extract_glossary(text, GlossaryConfig(rule_appositive=False)).as_dict() == {}


# --- locality gate (the λv trap, as a model actually produced it) -------------

FAR = (
    "The virions become deactivated at a rate λv that depends on humidity; the "
    "deactivation rate is bounded by data. " + "filler text " * 60
    + "The rate of ventilation is set by the HVAC system and the air change rate."
)


def test_locality_gate_rejects_words_scattered_across_the_paper() -> None:
    # All three words occur in the document, but not near λv.
    g, rejected = validate_entries([("λv", "rate of ventilation")], FAR, "llm/1")
    assert g.as_dict() == {} and rejected == 1
    g, _ = validate_entries([("λv", "rate of ventilation")], FAR, "llm/1",
                            GlossaryConfig(definition_window=0))
    assert g.as_dict() == {"λv": ["rate of ventilation"]}  # the owner's minimum gate alone


def test_locality_gate_keeps_a_paraphrase_near_the_symbol() -> None:
    g, rejected = validate_entries([("λv", "deactivation rate of virions")], FAR, "llm/1")
    assert g.as_dict() == {"λv": ["deactivation rate of virions"]} and rejected == 0


# --- review fixes (#754 PR 763) -----------------------------------------------


def test_appositive_does_not_steal_the_previous_clause() -> None:
    text = "where pm is the mask penetration factor, pf is the filtration efficiency"
    d = extract_glossary(text).as_dict()
    assert d.get("pm") == ["mask penetration factor"]
    assert "mask penetration factor" not in d.get("pf", [])
    assert d.get("pf") == ["filtration efficiency"]


def test_where_rule_allows_space_before_units_in_parentheses() -> None:
    d = extract_glossary("where Qb (L/min) is the breathing flow rate. Next.").as_dict()
    assert d == {"Qb": ["breathing flow rate"]}


@pytest.mark.parametrize(
    "text,ok",
    [("Qb = 0.5 m3/h", True), ("Qb=0.5", True), ("Qb ≈ 1.2 L/s", True), ("Qb ~ 30", True),
     ("λv(r) = 0.3/h", True), ("λ_v = .3", True), ("r¯=0.5 μm", True), ("Qb = −2", True),
     ("Qb (m3/h) = 0.5", True),
     ("Qb is large", False), ("Qb = Cq V", False), ("λ̄c = λa + λf", False),
     ("λs(r) < λa", False), ("Qbx = 1", False), ("the Qb value", False)],
)
def test_value_statement(text: str, ok: bool) -> None:
    from ragstack.ingestion.symbol_glossary import states_value

    sym = "λ̄c" if text.startswith("λ̄") else ("λs" if text.startswith("λs") else (
        "λv" if text.startswith("λ") else ("r¯" if text.startswith("r") else "Qb")))
    assert states_value(sym, text) is ok


def test_value_only_attaches_to_the_value_chunk_only() -> None:
    g = Glossary((_e("Qb", "breathing flow rate"),))
    assert g.for_chunk("we discuss Qb at length") == ""
    assert g.for_chunk("Table 1: Qb = 0.5 m3/h") == "Qb — breathing flow rate"
    assert g.for_chunk("we discuss Qb", GlossaryConfig(attach_only_with_value=False)) == (
        "Qb — breathing flow rate")


def test_near_but_wrong_definition_through_another_symbol_is_rejected() -> None:
    text = ("The air change rate λa and the viral deactivation rate λv are both "
            "first-order losses; λv = 0.3/h here.")
    g, rejected = validate_entries([("λv", "air change rate")], text, "llm/1")
    assert g.as_dict() == {} and rejected == 1
    g, _ = validate_entries([("λv", "viral deactivation rate"), ("λa", "air change rate")], text, "llm/1")
    assert g.as_dict() == {"λa": ["air change rate"], "λv": ["viral deactivation rate"]}


def test_near_but_wrong_paraphrase_is_a_known_limitation() -> None:
    # A paraphrase (not a verbatim copy) cannot be checked for "which symbol
    # does it sit next to": every word is near λv, so it passes. Documented.
    text = "The air change rate λa and the viral deactivation rate λv matter in each of the rooms."
    g, _ = validate_entries([("λv", "rate of air change")], text, "llm/1")
    assert g.as_dict() == {"λv": ["rate of air change"]}


def test_validated_offset_prefers_the_copy_nearest_the_symbol() -> None:
    text = ("deactivation rate is discussed in general terms. " + "x " * 300
            + "the deactivation rate λv is then fixed; λv = 0.3/h.")
    g, _ = validate_entries([("λv", "deactivation rate")], text, "llm/1")
    (d,) = g.entries[0].definitions
    assert d.offset == text.rindex("deactivation rate")
