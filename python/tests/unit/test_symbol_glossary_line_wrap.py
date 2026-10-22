"""#790: a definition wrapped across a single line break (PDF text keeps the
PDF's line breaks). rules/3 folds a single break into a space for definition
matching, de-hyphenates conservatively, keeps paragraph breaks as terminators,
and keeps raw offsets exact. All synthetic text — no fixture copies a paper."""
from __future__ import annotations

import pytest

from ragstack.ingestion.symbol_glossary import (
    EXTRACTOR_VERSION,
    Glossary,
    GlossaryConfig,
    canonical_map,
    canonical_symbol,
    definition_map,
    extract_glossary,
    validate_entries,
)

# --- the issue's examples, as a regression table ------------------------------

ISSUE_790 = [
    ("where Qb is the breathing flow rate. Next.", {"Qb": ["breathing flow rate"]}),
    ("where Qb is the breathing\nflow rate. Next.", {"Qb": ["breathing flow rate"]}),
    ("where Qb is the\nbreathing flow rate (exhaled volume). Next.",
     {"Qb": ["breathing flow rate (exhaled volume)"]}),
    ("where Qb is the breathing flow\nrate and Cq is the quanta. Next.",
     {"Qb": ["breathing flow rate"], "Cq": ["quanta"]}),
]


@pytest.mark.parametrize(("text", "expected"), ISSUE_790)
def test_issue_790_examples(text: str, expected: dict[str, list[str]]) -> None:
    assert extract_glossary(text).as_dict() == expected


def test_version_is_rules_3() -> None:
    assert EXTRACTOR_VERSION == "rules/3"
    assert extract_glossary(ISSUE_790[1][0]).version == "rules/3"


# --- every rule, wrapped ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # R1 where, plus its continuation clause wrapped too
        ("Here N = Qb Cq, where Qb is the breathing\nflow rate, and Cq is the quanta\n"
         "concentration.", {"Qb": ["breathing flow rate"], "Cq": ["quanta concentration"]}),
        # the symbol slot and the copula wrapped
        ("as follows, where\nQb\nis the breathing flow rate.", {"Qb": ["breathing flow rate"]}),
        # R2 appositive
        ("The second equality defines the effective infectious\ndrop radius r¯, given below.",
         {canonical_symbol("r¯"): ["effective infectious drop radius"]}),
        # R3 is-the
        ("The quantity λ¯c=λa+λf is the air\npurification rate of the room.",
         {canonical_symbol("λ¯c"): ["air purification rate of the room"]}),
        # R4 parenthetical
        ("We estimate the settling\nspeed (vs) from Stokes' law.", {"vs": ["settling speed"]}),
        # R5 respectively
        ("Here Qb, Cq and λv are the breathing flow rate, the quanta\nconcentration "
         "and the deactivation\nrate, respectively.",
         {"Cq": ["quanta concentration"], "Qb": ["breathing flow rate"], "λv": ["deactivation rate"]}),
    ],
    ids=["where", "where-symbol-slot", "appositive", "is-the", "parenthetical", "respectively"],
)
def test_each_rule_finds_a_wrapped_definition(text: str, expected: dict[str, list[str]]) -> None:
    assert extract_glossary(text).as_dict() == expected


@pytest.mark.parametrize("eol", ["\r\n", " \n", "\n\t", "  \n  "])
def test_line_break_variants_fold(eol: str) -> None:
    text = f"where Qb is the breathing{eol}flow rate. Next."
    assert extract_glossary(text).as_dict() == {"Qb": ["breathing flow rate"]}


# --- a paragraph break still ends a definition --------------------------------


@pytest.mark.parametrize("para", ["\n\n", "\n \n", "\r\n\r\n", "\n\t\n\n"])
def test_paragraph_break_still_ends_a_definition(para: str) -> None:
    text = f"where Qb is the breathing flow{para}rate of the room is high. Next."
    assert extract_glossary(text).as_dict() == {"Qb": ["breathing flow"]}


def test_paragraph_break_ends_a_respectively_list() -> None:
    text = ("Here Qb and Cq are the breathing flow rate\n\nand the quanta concentration, "
            "respectively.")
    assert extract_glossary(text, GlossaryConfig(rule_appositive=False)).as_dict() == {}


def test_paragraph_break_is_kept_in_the_definition_text() -> None:
    canon, _ = definition_map("a b\nc\n\nd\n \ne")
    assert canon == "a b c\n\nd\n \ne"


# --- the next clause still ends a definition (rules/3 _DEF_END) ----------------


def test_next_clause_with_arguments_or_value_ends_the_definition() -> None:
    # Before rules/3 only a line break that happened to fall before "Cp(t)" cut
    # Cs's definition; on one line it ran on and Cp was lost.
    text = ("where Cs is the initial tracer gas concentration and Cp(t) is the\n"
            "concentration at a point, λ (h−1) is the inactivation\nrate, "
            "η represents the purifier efficiency, and V is the volume.")
    assert extract_glossary(text).as_dict() == {
        "Cs": ["initial tracer gas concentration"],
        "Cp": ["concentration at a point"],
        "λ": ["inactivation rate"],
        "η": ["purifier efficiency"],
    }


def test_value_with_arguments_starts_the_next_clause() -> None:
    text = "where ρ=ρ(p,T) is the varying\ndensity and μ=μ(T) is\nthe dynamic viscosity. Next."
    assert extract_glossary(text).as_dict() == {
        "ρ": ["varying density"], "μ": ["dynamic viscosity"]}


# --- de-hyphenation -------------------------------------------------------------


def test_soft_hyphen_is_joined_when_the_document_spells_the_word() -> None:
    text = "where Qb is the breath-\ning flow rate. Breathing is measured."
    assert extract_glossary(text).as_dict() == {"Qb": ["breathing flow rate"]}


def test_hyphen_is_kept_when_the_joined_word_is_not_in_the_document() -> None:
    # "air-\nborne" is ambiguous (soft hyphen or compound); with no unbroken
    # "airborne" in the text the hyphen stays: no word is invented.
    assert extract_glossary("where Qb is the air-\nborne flow rate. Next.").as_dict() == {
        "Qb": ["air-borne flow rate"]}
    assert extract_glossary(
        "where Qb is the air-\nborne flow rate. Airborne spread matters."
    ).as_dict() == {"Qb": ["airborne flow rate"]}


def test_a_real_compound_split_at_its_hyphen_keeps_the_hyphen() -> None:
    text = "where γ is the state-\ndependent coefficient. Next."
    assert extract_glossary(text).as_dict() == {"γ": ["state-dependent coefficient"]}


@pytest.mark.parametrize(
    ("raw", "folded"),
    [
        ("SARS-\nCoV here", "SARS-CoV here"),      # uppercase on either side: kept
        ("Wells-\nRiley wellsriley", "Wells-Riley wellsriley"),
        ("Wells-\nriley wellsriley", "Wellsriley wellsriley"),  # only the letters at the break count
        ("x a-\nb ab", "x a-b ab"),                # one-letter fragment: algebra, kept
        ("breath-\n\ning breathing", "breath-\n\ning breathing"),  # paragraph: untouched
        ("rate -\nflow", "rate - flow"),           # a dash, not a hyphenated word
        ("breath-\n  ing breathing", "breathing breathing"),
        ("breath- \ning breathing", "breathing breathing"),
    ],
)
def test_dehyphenation_cases(raw: str, folded: str) -> None:
    assert definition_map(raw)[0] == folded


# --- raw offsets stay exact -----------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "where Qb is the breathing\nflow rate.",
        "A λ̄c\r\nis the breath-\ning rate; breathing\n\nends \\lambda_v here\n",
        "µ-\nm and r¯\n  \tx-\nyz  \n\n\n q",
        "COVID-\n19 and SARS-CoV-\n 2, a 2-\ndimensional ﬁ-\nlter, ﬁlter\n",
    ],
)
def test_definition_map_offsets_are_exact(raw: str) -> None:
    """Strict alignment: walking the canonical text in order, every character
    of the folded text is EITHER the next kept canonical character (same
    character, same raw offset) OR a folded space standing for a whitespace run
    with one newline, mapped to the run's first raw offset. Everything skipped
    is whitespace or a dropped soft hyphen."""
    canon, cpos = canonical_map(raw)
    folded, pos = definition_map(raw)
    assert len(pos) == len(folded) + 1 and pos[-1] == len(raw) == cpos[-1]
    j = 0
    for i, ch in enumerate(folded):
        if canon[j] == ch and cpos[j] == pos[i]:
            j += 1  # a kept character, at its own raw offset
            continue
        if ch == " " and canon[j].isspace() and cpos[j] == pos[i]:
            # a folded line break: ONE space for a whitespace run with ONE newline
            k = j
            while k < len(canon) and canon[k].isspace():
                k += 1
            assert canon[j:k].count("\n") == 1, (i, canon[j:k])
            j = k
            continue
        # dropped: a soft hyphen and its break (joined word), or the break after
        # a kept hyphen; then the next folded character is the next canonical one
        k = j + 1 if canon[j] == "-" else j
        w = k
        while k < len(canon) and canon[k].isspace():
            k += 1
        assert k > j and canon[w:k].count("\n") == 1, (i, ch, canon[j:k])
        assert canon[k] == ch and cpos[k] == pos[i], (i, ch, k)
        j = k + 1
    assert j == len(canon)  # nothing left over: every canonical character accounted for


def test_definition_offsets_are_raw_offsets_after_folding() -> None:
    raw = ("Intro line\nwraps here.\n\nwhere Qb is the breathing\nflow rate. More text\n"
           "follows on the next line.\n\nLater, where Qb is the\nbasal flow rate. End.")
    g = extract_glossary(raw)
    (entry,) = g.entries
    first, second = (raw.index("Qb"), raw.index("Qb", raw.index("Qb") + 1))
    assert [(d.text, d.offset) for d in entry.definitions] == [
        ("breathing flow rate", first), ("basal flow rate", second)]
    # positional attachment picks by those raw offsets
    chunk = "Qb = 0.5 m3/h"
    assert g.for_chunk(chunk, start_char=first + 5) == "Qb — breathing flow rate"
    assert g.for_chunk(chunk, start_char=second + 1) == "Qb — basal flow rate"


def test_offsets_after_a_dehyphenated_word() -> None:
    raw = "Breathing.\nThe breath-\ning flow rate (Qb) and the dose-\nresponse rate (Cq)."
    g = extract_glossary(raw)
    assert g.as_dict() == {"Cq": ["dose-response rate"], "Qb": ["breathing flow rate"]}
    offsets = {e.symbol: e.definitions[0].offset for e in g.entries}
    assert offsets == {"Qb": raw.index("Qb"), "Cq": raw.index("Cq")}


# --- value statements and symbol matching on a wrapped chunk --------------------


@pytest.mark.parametrize(
    "chunk",
    ["The rate Qb =\n0.5 m3/h.", "The rate Qb\n= 0.5 m3/h.", "Qb\n≈\n.5", "Qb (L/min) =\n0.5",
     "Qb =\r\n-0.5"],
)
def test_value_statement_wrapped_across_a_line_attaches(chunk: str) -> None:
    g = extract_glossary("where Qb is the breathing\nflow rate. Next.")
    assert g.for_chunk(chunk) == "Qb — breathing flow rate"


def test_wrapped_bare_equation_still_does_not_attach() -> None:
    g = extract_glossary("where Qb is the breathing\nflow rate. Next.")
    assert g.for_chunk("so that Qb =\nVb f") == ""
    assert g.for_chunk("so that Qb =\nVb f", GlossaryConfig(attach_only_with_value=False)) != ""


# --- the LLM validation gates are unchanged --------------------------------------


def test_locality_gate_unchanged_on_wrapped_text() -> None:
    doc = ("The virions become deactivated at a\nrate λv (the deactivation\nrate) that depends on humidity.\n"
           + "filler text\n" * 60
           + "The rate of ventilation is set by the HVAC\nsystem and the air change rate.")
    g, rejected = validate_entries([("λv", "rate of ventilation")], doc, "llm/1")
    assert g.as_dict() == {} and rejected == 1
    g, rejected = validate_entries([("λv", "deactivation rate")], doc, "llm/1")
    assert g.as_dict() == {"λv": ["deactivation rate"]} and rejected == 0


def test_validated_wrapped_verbatim_definition_gets_its_raw_offset() -> None:
    doc = "Text. where Qb is the breathing\nflow rate. More."
    g, _ = validate_entries([("Qb", "breathing flow rate")], doc, "llm/1")
    assert isinstance(g, Glossary)
    assert g.entries[0].definitions[0].offset == doc.index("breathing")


# --- folding makes runs without a sentence end longer: stay linear ------------


def test_long_run_without_a_sentence_end_stays_fast() -> None:
    import time

    for text in ("where Qb is the\n" * 5000, "where Qb is the " * 5000, "ab-\ncd " * 20000):
        t0 = time.perf_counter()
        extract_glossary(text)
        assert time.perf_counter() - t0 < 2.0  # quadratic was ~3.5 s on the first


# --- review of #794 ------------------------------------------------------------


def test_continuation_reach_is_not_a_terminator() -> None:
    """One "where" sentence far longer than the continuation window: every
    clause is found whole, none is cut at a window boundary."""
    from ragstack.ingestion.symbol_glossary import _CONTINUATION_MARGIN, _CONTINUATION_REACH

    n = 300
    clauses = ", ".join(f"x{k} denotes the mass quantity number {k}" for k in range(2, n + 1))
    text = "where x1 is the mass quantity number 1, " + clauses + ". End."
    assert len(text) > 2 * (_CONTINUATION_REACH + _CONTINUATION_MARGIN)
    got = extract_glossary(text, GlossaryConfig(rule_is_the=False)).as_dict()
    assert got == {f"x{k}": [f"mass quantity number {k}"] for k in range(1, n + 1)}


def test_a_window_cut_inside_a_clause_is_not_emitted_truncated() -> None:
    from ragstack.ingestion import symbol_glossary as sg

    # place a clause so the first window ends inside its definition
    filler = ", ".join(f"x{k} denotes the mass quantity number {k}" for k in range(2, 400))
    text = "where x1 is the mass quantity number 1, " + filler + ". End."
    canon = sg.definition_map(text)[0]
    m = next(sg._WHERE.finditer(canon))
    for c in sg._continuations(canon, m.end("def"), len(canon) - 6):
        assert c.group("def").startswith("the mass quantity number ")
        assert c.group("def").split()[-1].isdigit()
        assert canon[c.end() : c.end() + 2] in (", ", ". ")


@pytest.mark.parametrize(
    ("raw", "folded"),
    [
        ("COVID-\n19 cases", "COVID-19 cases"),
        ("SARS-CoV-\n2 virus", "SARS-CoV-2 virus"),
        ("a 2-\ndimensional flow", "a 2-dimensional flow"),
        ("N-\n95 mask", "N-95 mask"),
        ("COVID-\n\n19", "COVID-\n\n19"),  # a paragraph break is still kept
    ],
)
def test_hyphen_next_to_a_digit_keeps_the_hyphen_without_a_space(raw: str, folded: str) -> None:
    assert definition_map(raw)[0] == folded


def test_appositive_does_not_steal_a_clause_with_arguments() -> None:
    text = "We set the initial concentration, Cp(t) is the point concentration. Next."
    assert extract_glossary(text).as_dict() == {"Cp": ["point concentration"]}
    # without a clause of its own the appositive still reads (and keeps) the args
    assert extract_glossary("the initial concentration Cp(t) at the inlet").as_dict() == {
        "Cp": ["initial concentration"]}
    # a predicate that is not a definition does not block the appositive
    assert extract_glossary(
        "Here the instantaneous pedestrian density ρ(t) is a major factor. The social "
        "distance index Pd (%) is expressed as a function of distance.").as_dict() == {
        "Pd": ["social distance index"], "ρ": ["instantaneous pedestrian density"]}


@pytest.mark.parametrize(
    "text",
    [
        "Qb" + " " * 200_000 + "and Cq are the rates, respectively.",
        "the breathing flow rate" + " " * 200_000 + "Qb here.",
        "where Qb is the" + " " * 200_000 + "rate.",
        "Qb, Cq" + " " * 200_000 + "x",
    ],
)
def test_long_space_runs_stay_fast(text: str) -> None:
    import time

    t0 = time.perf_counter()
    extract_glossary(text)
    assert time.perf_counter() - t0 < 1.0
