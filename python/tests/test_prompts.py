"""Unit tests for ``ragstack.prompts`` — ADR-0008's template loader/renderer.

Two clusters carry most of the weight:

* **Load-time validation.** ADR-0008 decision 3 rule 5 makes a malformed file a
  boot failure. Every rule gets a test that writes a file which breaks exactly
  that rule and asserts the loader refuses it — because the whole value of
  load-time validation is that it is exhaustive; one rule enforced at request
  time instead is the 500 the ADR set out to avoid.
* **Substitution safety.** Rule 1 ("values, never source") and the single-pass
  requirement are asserted directly, with values that would expand under any
  renderer that re-scans its own output.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from ragstack.prompts import (
    PromptTemplate,
    Slot,
    TemplateRenderError,
    TemplateValidationError,
    load_templates,
    render,
    to_wire,
)

_REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text_record(**over: Any) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "id": "basic",
        "version": 1,
        "label": "Basic answer",
        "output": "text",
        "slots": [{"name": "focus", "required": False, "max_len": 80}],
        "system": "You answer from the passages only.",
        "user": "Context:\n{{context}}\n\n{{#focus}}Focus on: {{focus}}\n{{/focus}}Answer.",
    }
    rec.update(over)
    return {k: v for k, v in rec.items() if v is not _ABSENT}


class _Absent:
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<absent>"


#: Sentinel for "this key is not in the record at all", which is a different
#: failure from "this key is present and wrong".
_ABSENT = _Absent()


def _write(tmp_path: Path, records: list[dict[str, Any]], name: str = "templates.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(records), encoding="utf-8")
    return p


def _load_one(tmp_path: Path, **over: Any) -> PromptTemplate:
    return load_templates(_write(tmp_path, [_text_record(**over)]))["basic"]


# ---------------------------------------------------------------------------
# File-level loading
# ---------------------------------------------------------------------------


def test_loads_a_json_file(tmp_path: Path) -> None:
    t = _load_one(tmp_path)
    assert t.id == "basic"
    assert t.version == 1
    assert t.output == "text"
    assert t.columns is None
    assert t.slots == (Slot(name="focus", required=False, max_len=80),)
    assert len(t.hash) == 16


def test_loads_a_yaml_file(tmp_path: Path) -> None:
    yaml = pytest.importorskip("yaml")
    p = tmp_path / "templates.yaml"
    p.write_text(yaml.safe_dump([_text_record()]), encoding="utf-8")
    assert load_templates(p)["basic"].label == "Basic answer"


def test_yaml_and_json_spellings_of_the_same_file_agree_on_the_hash(tmp_path: Path) -> None:
    """The hash names a *condition*; it must not depend on which serialization
    the operator happened to write the template in."""
    yaml = pytest.importorskip("yaml")
    (tmp_path / "a.yaml").write_text(yaml.safe_dump([_text_record()]), encoding="utf-8")
    from_json = _load_one(tmp_path)
    from_yaml = load_templates(tmp_path / "a.yaml")["basic"]
    assert from_json.hash == from_yaml.hash


def test_missing_file_is_a_validation_error(tmp_path: Path) -> None:
    with pytest.raises(TemplateValidationError, match="not found"):
        load_templates(tmp_path / "nope.json")


def test_unsupported_suffix_is_refused(tmp_path: Path) -> None:
    p = tmp_path / "templates.txt"
    p.write_text("[]", encoding="utf-8")
    with pytest.raises(TemplateValidationError, match="unsupported template file suffix"):
        load_templates(p)


def test_malformed_json_names_the_file(tmp_path: Path) -> None:
    p = tmp_path / "templates.json"
    p.write_text("[{", encoding="utf-8")
    with pytest.raises(TemplateValidationError, match="not valid JSON"):
        load_templates(p)


def test_empty_file_loads_as_no_templates(tmp_path: Path) -> None:
    p = tmp_path / "templates.yaml"
    p.write_text("", encoding="utf-8")
    pytest.importorskip("yaml")
    assert load_templates(p) == {}


def test_top_level_must_be_a_list(tmp_path: Path) -> None:
    p = tmp_path / "templates.json"
    p.write_text(json.dumps({"templates": [_text_record()]}), encoding="utf-8")
    with pytest.raises(TemplateValidationError, match="expected a list of template records"):
        load_templates(p)


def test_duplicate_template_ids_are_refused(tmp_path: Path) -> None:
    p = _write(tmp_path, [_text_record(), _text_record(label="Other")])
    with pytest.raises(TemplateValidationError, match="duplicate template id 'basic'"):
        load_templates(p)


def test_a_record_that_is_not_a_mapping_is_refused(tmp_path: Path) -> None:
    p = tmp_path / "templates.json"
    p.write_text(json.dumps(["ppi-extraction"]), encoding="utf-8")
    with pytest.raises(TemplateValidationError, match="expected a mapping"):
        load_templates(p)


def test_unknown_template_key_is_refused(tmp_path: Path) -> None:
    """`slot:` for `slots:` is the realistic typo; tolerated, it would load a
    template with no declared slots and therefore no length caps."""
    with pytest.raises(TemplateValidationError, match=r"unknown key\(s\) \['slot'\]"):
        _load_one(tmp_path, slot=[{"name": "focus", "required": False, "max_len": 8}])


# ---------------------------------------------------------------------------
# Field validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["id", "version", "label", "output", "system", "user"])
def test_required_fields_must_be_present(tmp_path: Path, field: str) -> None:
    with pytest.raises(TemplateValidationError, match=field):
        _load_one(tmp_path, **{field: _ABSENT})


@pytest.mark.parametrize("bad", ["", "  ", 7, None, " padded"])
def test_bad_ids_are_refused(tmp_path: Path, bad: Any) -> None:
    with pytest.raises(TemplateValidationError, match="'id'"):
        _load_one(tmp_path, id=bad)


@pytest.mark.parametrize("bad", [0, -1, "1", 1.0, True, None])
def test_version_must_be_an_int_at_least_one(tmp_path: Path, bad: Any) -> None:
    with pytest.raises(TemplateValidationError, match="'version'"):
        _load_one(tmp_path, version=bad)


@pytest.mark.parametrize("bad", ["tsv", "TEXT", "", None, 1])
def test_output_must_be_text_or_table(tmp_path: Path, bad: Any) -> None:
    with pytest.raises(TemplateValidationError, match="'output'"):
        _load_one(tmp_path, output=bad)


@pytest.mark.parametrize("bad", ["", None, 3])
def test_label_must_be_a_non_empty_string(tmp_path: Path, bad: Any) -> None:
    with pytest.raises(TemplateValidationError, match="'label'"):
        _load_one(tmp_path, label=bad)


def test_table_output_requires_non_empty_columns(tmp_path: Path) -> None:
    with pytest.raises(TemplateValidationError, match="requires a non-empty list"):
        _load_one(tmp_path, output="table", user="{{context}}\n{{#focus}}{{focus}}{{/focus}}")
    with pytest.raises(TemplateValidationError, match="requires a non-empty list"):
        _load_one(
            tmp_path,
            output="table",
            columns=[],
            user="{{context}}\n{{#focus}}{{focus}}{{/focus}}",
        )


def test_table_columns_must_be_non_empty_strings(tmp_path: Path) -> None:
    with pytest.raises(TemplateValidationError, match="non-empty string"):
        _load_one(
            tmp_path,
            output="table",
            columns=["Gene", ""],
            user="{{context}}\n{{#focus}}{{focus}}{{/focus}}",
        )


def test_text_output_must_not_declare_columns(tmp_path: Path) -> None:
    """Silently ignoring them would ship prose from a template whose author
    plainly meant a table."""
    with pytest.raises(TemplateValidationError, match="must not declare columns"):
        _load_one(tmp_path, columns=["Gene"])


# ---------------------------------------------------------------------------
# Slot declarations
# ---------------------------------------------------------------------------


def test_slot_without_max_len_fails_at_load(tmp_path: Path) -> None:
    """ADR-0008 decision 3 rule 2: an undeclared cap is an authoring error, and
    it fails at load rather than letting an unbounded value through at request
    time."""
    with pytest.raises(TemplateValidationError, match="must declare an integer cap"):
        _load_one(tmp_path, slots=[{"name": "focus", "required": False}])


@pytest.mark.parametrize("bad", [0, -5, "80", 80.0, None, True])
def test_bad_max_len_values_fail_at_load(tmp_path: Path, bad: Any) -> None:
    # `True` is in this list on purpose: `isinstance(True, int)` is True in
    # Python and YAML decodes `max_len: yes` to a bool, so a naive int check
    # would silently cap the slot at one character.
    with pytest.raises(TemplateValidationError, match="'max_len'"):
        _load_one(tmp_path, slots=[{"name": "focus", "required": False, "max_len": bad}])


@pytest.mark.parametrize("bad", ["Focus", "1focus", "focus-name", "focus name", "", "_focus"])
def test_slot_names_must_be_lowercase_identifiers(tmp_path: Path, bad: str) -> None:
    with pytest.raises(TemplateValidationError, match="'name'"):
        _load_one(tmp_path, slots=[{"name": bad, "required": False, "max_len": 8}])


def test_duplicate_slot_names_are_refused(tmp_path: Path) -> None:
    with pytest.raises(TemplateValidationError, match="duplicate slot name 'focus'"):
        _load_one(
            tmp_path,
            slots=[
                {"name": "focus", "required": False, "max_len": 8},
                {"name": "focus", "required": True, "max_len": 9},
            ],
        )


@pytest.mark.parametrize("name", ["context", "columns", "label"])
def test_a_slot_may_not_take_a_reserved_name(tmp_path: Path, name: str) -> None:
    """A slot named `context` would be settable via template_vars — which is how
    a caller would swap the retrieved passages for text of its own."""
    with pytest.raises(TemplateValidationError, match="is reserved"):
        _load_one(
            tmp_path,
            slots=[{"name": name, "required": False, "max_len": 8}],
            user="{{context}}{{#focus}}x{{/focus}}",
        )


def test_required_must_be_a_boolean(tmp_path: Path) -> None:
    with pytest.raises(TemplateValidationError, match="'required'"):
        _load_one(tmp_path, slots=[{"name": "focus", "required": "yes", "max_len": 8}])


def test_unknown_slot_key_is_refused(tmp_path: Path) -> None:
    with pytest.raises(TemplateValidationError, match=r"unknown key\(s\) \['maxlen'\]"):
        _load_one(
            tmp_path,
            slots=[{"name": "focus", "required": False, "max_len": 8, "maxlen": 9}],
        )


# ---------------------------------------------------------------------------
# Body <-> slot agreement
# ---------------------------------------------------------------------------


def test_undeclared_slot_reference_fails_at_load(tmp_path: Path) -> None:
    with pytest.raises(TemplateValidationError, match=r"undeclared slot\(s\) \['organism'\]"):
        _load_one(tmp_path, user="{{context}} about {{organism}} {{#focus}}{{focus}}{{/focus}}")


def test_declared_but_unreferenced_slot_fails_at_load(tmp_path: Path) -> None:
    """Chosen over a warning: the server would otherwise ADVERTISE a knob in
    GET /v1/prompt-templates, accept a value for it, and change nothing — rule
    3's silent-wrong-prompt failure, invited by the server itself."""
    with pytest.raises(TemplateValidationError, match="declared but never referenced"):
        _load_one(
            tmp_path,
            slots=[
                {"name": "focus", "required": False, "max_len": 8},
                {"name": "unused", "required": False, "max_len": 8},
            ],
        )


def test_a_slot_used_only_as_a_section_condition_counts_as_referenced(tmp_path: Path) -> None:
    t = _load_one(tmp_path, user="{{context}}{{#focus}} (focused){{/focus}}")
    assert render(t, {"focus": "kinases"}, "P")[1] == "P (focused)"


def test_markers_in_system_fail_at_load(tmp_path: Path) -> None:
    """Rule 4. `system` is emitted verbatim, so a marker there would reach the
    model as literal braces — the author believed it would be filled."""
    with pytest.raises(TemplateValidationError, match="no substitution markers"):
        _load_one(tmp_path, system="You answer about {{focus}}.")


def test_even_a_reserved_marker_in_system_fails(tmp_path: Path) -> None:
    with pytest.raises(TemplateValidationError, match="no substitution markers"):
        _load_one(tmp_path, system="Passages:\n{{context}}")


@pytest.mark.parametrize(
    "body",
    [
        "{{context}} {{ focus }}",  # internal whitespace
        "{{context}} {{Focus}}",  # wrong case
        "{{context}} {{{focus}}}",  # triple braces
        "{{context}} {{focus",  # unterminated
        "{{context}} {{focus-name}}",  # hyphen
    ],
)
def test_near_markers_are_errors_not_literal_text(tmp_path: Path, body: str) -> None:
    """Rendering these as literal text is exactly the outcome rule 3 forbids:
    the prompt renders, the model answers, and nothing says the value never
    arrived."""
    with pytest.raises(TemplateValidationError, match="unparsable substitution marker"):
        _load_one(tmp_path, user=body)


def test_unclosed_section_fails_at_load(tmp_path: Path) -> None:
    with pytest.raises(TemplateValidationError, match="never closed"):
        _load_one(tmp_path, user="{{context}}{{#focus}}{{focus}}")


def test_mismatched_section_close_fails_at_load(tmp_path: Path) -> None:
    with pytest.raises(TemplateValidationError, match="closes section 'focus'"):
        _load_one(
            tmp_path,
            slots=[
                {"name": "focus", "required": False, "max_len": 8},
                {"name": "other", "required": False, "max_len": 8},
            ],
            user="{{context}}{{#focus}}{{focus}}{{/other}}{{#other}}{{other}}{{/other}}",
        )


def test_stray_section_close_fails_at_load(tmp_path: Path) -> None:
    with pytest.raises(TemplateValidationError, match="never opened"):
        _load_one(tmp_path, user="{{context}}{{focus}}{{/focus}}")


def test_a_reserved_name_cannot_open_a_section(tmp_path: Path) -> None:
    with pytest.raises(TemplateValidationError, match="cannot open a conditional section"):
        _load_one(tmp_path, user="{{#context}}{{context}}{{/context}}{{#focus}}{{focus}}{{/focus}}")


def test_columns_marker_in_a_text_template_fails_at_load(tmp_path: Path) -> None:
    with pytest.raises(TemplateValidationError, match=r"references \{\{columns\}\}"):
        _load_one(tmp_path, user="{{columns}}\n{{context}}{{#focus}}{{focus}}{{/focus}}")


def test_a_template_that_never_uses_context_loads_but_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A warning rather than an error: refusing it would be this module
    inventing a rule ADR-0008 does not state, but a RAG prompt that never
    receives its passages generates ungrounded text and should say so at boot."""
    with caplog.at_level(logging.WARNING, logger="ragstack.prompts"):
        t = _load_one(tmp_path, user="Summarize: {{#focus}}{{focus}}{{/focus}}")
    assert t.id == "basic"
    assert "does not reference" in caplog.text


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_system_is_returned_verbatim(tmp_path: Path) -> None:
    t = _load_one(tmp_path)
    system, _ = render(t, {"focus": "kinases"}, "PASSAGES")
    assert system == "You answer from the passages only."


def test_reserved_names_resolve_from_server_state(tmp_path: Path) -> None:
    t = _load_one(
        tmp_path,
        output="table",
        columns=["Gene", "Function"],
        user="{{label}}\n{{columns}}\n{{context}}{{#focus}} [{{focus}}]{{/focus}}",
    )
    _, user = render(t, {}, "P1\n\nP2")
    assert user == "Basic answer\nGene\tFunction\nP1\n\nP2"


def test_optional_slot_absent_drops_its_whole_section(tmp_path: Path) -> None:
    t = _load_one(tmp_path, user="{{context}}{{#focus}} focusing on {{focus}}{{/focus}}.")
    assert render(t, {}, "P")[1] == "P."
    assert render(t, {"focus": "kinases"}, "P")[1] == "P focusing on kinases."


def test_optional_slot_absent_renders_a_bare_marker_as_empty(tmp_path: Path) -> None:
    t = _load_one(tmp_path, user="{{context}}|{{focus}}|")
    assert render(t, {}, "P")[1] == "P||"


def test_empty_string_is_treated_as_absent(tmp_path: Path) -> None:
    t = _load_one(tmp_path, user="{{context}}{{#focus}} focusing on {{focus}}{{/focus}}.")
    assert render(t, {"focus": ""}, "P")[1] == "P."


def test_missing_required_slot_raises(tmp_path: Path) -> None:
    t = _load_one(tmp_path, slots=[{"name": "focus", "required": True, "max_len": 80}])
    with pytest.raises(TemplateRenderError) as exc:
        render(t, {}, "P")
    assert exc.value.slot == "focus"
    assert exc.value.template_id == "basic"


def test_empty_value_for_a_required_slot_raises(tmp_path: Path) -> None:
    """An explicit "" would render the identical empty clause a missing value
    renders, so accepting it reopens rule 3's hole through the front door."""
    t = _load_one(tmp_path, slots=[{"name": "focus", "required": True, "max_len": 80}])
    with pytest.raises(TemplateRenderError, match="non-empty value"):
        render(t, {"focus": ""}, "P")


def test_undeclared_var_raises_rather_than_being_ignored(tmp_path: Path) -> None:
    """Rule 3. The API maps this to 422; silently ignoring a typo'd name renders
    an empty clause and a subtly wrong prompt with no error anywhere."""
    t = _load_one(tmp_path)
    with pytest.raises(TemplateRenderError, match="does not declare"):
        render(t, {"focs": "kinases"}, "P")


@pytest.mark.parametrize("name", ["context", "columns", "label"])
def test_a_reserved_name_in_vars_raises_like_any_undeclared_key(tmp_path: Path, name: str) -> None:
    """Reserved names are not slots. If `context` were settable, a caller could
    substitute its own passages for the retrieved ones — level 3, refused."""
    t = _load_one(tmp_path)
    with pytest.raises(TemplateRenderError, match="does not declare"):
        render(t, {name: "mine"}, "P")


def test_value_over_max_len_raises(tmp_path: Path) -> None:
    t = _load_one(tmp_path, slots=[{"name": "focus", "required": False, "max_len": 5}])
    with pytest.raises(TemplateRenderError, match="over its declared max_len"):
        render(t, {"focus": "123456"}, "P")


def test_value_exactly_at_max_len_is_accepted(tmp_path: Path) -> None:
    t = _load_one(tmp_path, slots=[{"name": "focus", "required": False, "max_len": 5}])
    assert "12345" in render(t, {"focus": "12345"}, "P")[1]


def test_non_string_value_raises(tmp_path: Path) -> None:
    """Values arrive from JSON, where `{"focus": 2025}` is an int; coercing would
    put an unbounded repr into the prompt past the declared cap."""
    t = _load_one(tmp_path)
    with pytest.raises(TemplateRenderError, match="must be a string"):
        render(t, {"focus": 2025}, "P")  # type: ignore[dict-item]


# ---------------------------------------------------------------------------
# Rule 1 — values, never source
# ---------------------------------------------------------------------------


def test_a_marker_inside_a_value_stays_inert(tmp_path: Path) -> None:
    """ADR-0008 decision 3 rule 1. The literal text `{{context}}` must survive to
    the prompt; expanding it would let a caller pull the passage block into an
    instruction position it chose."""
    t = _load_one(tmp_path, slots=[{"name": "focus", "required": False, "max_len": 80}])
    _, user = render(t, {"focus": "{{context}}"}, "SECRET PASSAGES")
    assert "Focus on: {{context}}" in user
    # It appears exactly once — where {{context}} genuinely is in the body.
    assert user.count("SECRET PASSAGES") == 1


def test_a_section_marker_inside_a_value_stays_inert(tmp_path: Path) -> None:
    t = _load_one(tmp_path, slots=[{"name": "focus", "required": False, "max_len": 80}])
    _, user = render(t, {"focus": "{{#focus}}nested{{/focus}}"}, "P")
    assert "Focus on: {{#focus}}nested{{/focus}}" in user


def test_substitution_is_single_pass(tmp_path: Path) -> None:
    """The first slot's value spells out the second slot's marker. A renderer
    that re-scanned its own output would expand it on the second pass; this one
    has no second pass."""
    t = _load_one(
        tmp_path,
        slots=[
            {"name": "alpha", "required": False, "max_len": 80},
            {"name": "beta", "required": False, "max_len": 80},
        ],
        user="{{context}}|{{alpha}}|{{beta}}",
    )
    _, user = render(t, {"alpha": "{{beta}}", "beta": "BETA"}, "P")
    assert user == "P|{{beta}}|BETA"


def test_a_lone_open_brace_pair_in_a_value_cannot_capture_later_text(tmp_path: Path) -> None:
    """A value ending in a dangling `{{` would, under a re-rendering engine,
    swallow the template text that follows it."""
    t = _load_one(
        tmp_path,
        slots=[{"name": "focus", "required": False, "max_len": 80}],
        user="{{context}}[{{focus}}]TAIL",
    )
    _, user = render(t, {"focus": "danger {{"}, "P")
    assert user == "P[danger {{]TAIL"


# ---------------------------------------------------------------------------
# Content hash (ADR-0008 decision 4)
# ---------------------------------------------------------------------------


def test_hash_is_stable_for_identical_content(tmp_path: Path) -> None:
    """Same content, different file, different load — same hash. Without this the
    hash cannot do the job decision 4 gives it: comparing two tenants' copies."""
    other = tmp_path / "second-tenant"
    other.mkdir()
    a = _load_one(tmp_path)
    b = load_templates(_write(other, [_text_record()]))["basic"]
    assert a.hash == b.hash


@pytest.mark.parametrize(
    "over",
    [
        {"version": 2},
        {"system": "You answer from the passages only!"},
        {"user": "Context:\n{{context}}\n\n{{#focus}}Focus on: {{focus}}\n{{/focus}}Answer!"},
        {"slots": [{"name": "focus", "required": True, "max_len": 80}]},
        {"slots": [{"name": "focus", "required": False, "max_len": 81}]},
    ],
)
def test_any_content_change_changes_the_hash(tmp_path: Path, over: dict[str, Any]) -> None:
    assert _load_one(tmp_path).hash != _load_one(tmp_path, **over).hash


def test_renaming_a_slot_changes_the_hash(tmp_path: Path) -> None:
    base = _load_one(tmp_path)
    renamed = _load_one(
        tmp_path,
        slots=[{"name": "topic", "required": False, "max_len": 80}],
        user="Context:\n{{context}}\n\n{{#topic}}Focus on: {{topic}}\n{{/topic}}Answer.",
    )
    assert base.hash != renamed.hash


def test_reordering_slots_changes_the_hash(tmp_path: Path) -> None:
    """Deliberately conservative: reordering cannot change a rendered prompt, so
    this reports drift that does not affect output rather than risking the
    reverse."""
    body = "{{context}}{{#alpha}}{{alpha}}{{/alpha}}{{#beta}}{{beta}}{{/beta}}"
    a = {"name": "alpha", "required": False, "max_len": 8}
    b = {"name": "beta", "required": False, "max_len": 8}
    first = _load_one(tmp_path, slots=[a, b], user=body)
    second = _load_one(tmp_path, slots=[b, a], user=body)
    assert first.hash != second.hash


def test_changing_columns_changes_the_hash(tmp_path: Path) -> None:
    body = "{{columns}}\n{{context}}{{#focus}}{{focus}}{{/focus}}"
    one = _load_one(tmp_path, output="table", columns=["A", "B"], user=body)
    two = _load_one(tmp_path, output="table", columns=["A", "C"], user=body)
    assert one.hash != two.hash


def test_label_IS_content_because_it_renders(tmp_path: Path) -> None:
    """`label` is in RESERVED_NAMES, so `{{label}}` renders into the USER
    MESSAGE — three of the four templates we ship use it that way.

    This test previously asserted the opposite, and was only green because its
    fixture's body happened not to reference `{{label}}` while `_ppi_record` in
    this same file does. Under that definition a label edit changed the prompt
    the model saw and left the hash identical: two tenants holding the same
    (id, version, hash) with materially different instructions — verbatim the
    drift ADR-0008 decision 4 exists to make visible.

    A string that can reach the model is content, whatever it is called."""
    body = "{{context}} about {{label}} {{focus}}"
    base = _load_one(tmp_path, user=body)
    relabelled = _load_one(tmp_path, label="A different picker entry", user=body)

    system_a, user_a = render(base, {}, "CTX")
    system_b, user_b = render(relabelled, {}, "CTX")
    assert user_a != user_b, "the rendered prompt differs"
    assert base.hash != relabelled.hash, "so the hash must differ too"


def test_a_label_edit_is_caught_even_when_the_body_does_not_use_it(tmp_path: Path) -> None:
    """Conservative on purpose: the hash covers `label` unconditionally rather
    than only when `{{label}}` appears in the body.

    Deciding per-template would mean the same field is content in one template
    and not in another — a rule nobody can hold in their head while copying a
    file between tenants, which is exactly when this signal has to be trusted.
    Over-reporting drift that cannot affect output is the safe direction; the
    slot-level `label` stays excluded because it genuinely cannot render."""
    base = _load_one(tmp_path, user="{{context}} {{focus}}")
    relabelled = _load_one(tmp_path, label="Renamed", user="{{context}} {{focus}}")
    assert base.hash != relabelled.hash


def test_slot_label_is_not_content_either(tmp_path: Path) -> None:
    base = _load_one(tmp_path)
    labelled = _load_one(
        tmp_path, slots=[{"name": "focus", "required": False, "max_len": 80, "label": "Focus"}]
    )
    assert labelled.slots[0].label == "Focus"
    assert base.hash == labelled.hash


def test_hash_cannot_be_forged_by_shifting_a_boundary(tmp_path: Path) -> None:
    """Concatenating fields end to end would hash `system="ab", user="c"` and
    `system="a", user="bc"` identically; the canonical JSON encoding cannot."""
    one = _load_one(tmp_path, system="ab", user="c{{context}}{{#focus}}{{focus}}{{/focus}}")
    two = _load_one(tmp_path, system="a", user="bc{{context}}{{#focus}}{{focus}}{{/focus}}")
    assert one.hash != two.hash


# ---------------------------------------------------------------------------
# Wire projection
# ---------------------------------------------------------------------------


def test_to_wire_omits_the_prompt_bodies(tmp_path: Path) -> None:
    """Prompt bodies are operator configuration. Publishing them to every
    authenticated caller hands out the tenant's tuning and turns a template into
    something to paste into the level-3 request ADR-0008 refused to build."""
    item = to_wire(_load_one(tmp_path))
    assert "system" not in item
    assert "user" not in item


def test_to_wire_text_template_shape(tmp_path: Path) -> None:
    item = to_wire(_load_one(tmp_path))
    assert item == {
        "id": "basic",
        "version": 1,
        "hash": item["hash"],
        "label": "Basic answer",
        "output": "text",
        "slots": [{"name": "focus", "required": False, "max_len": 80}],
    }
    # `columns` is omitted, not null: the schema types it `array`, so `null`
    # would fail validation on a `text` template.
    assert "columns" not in item


def test_to_wire_table_template_carries_columns_and_slot_labels(tmp_path: Path) -> None:
    t = _load_one(
        tmp_path,
        output="table",
        columns=["Gene", "Function"],
        slots=[{"name": "focus", "required": True, "max_len": 80, "label": "Focus"}],
        user="{{columns}}\n{{context}}\n{{focus}}",
    )
    item = to_wire(t)
    assert item["columns"] == ["Gene", "Function"]
    assert item["slots"] == [
        {"name": "focus", "required": True, "max_len": 80, "label": "Focus"}
    ]


def test_to_wire_validates_against_the_published_schema(tmp_path: Path) -> None:
    """The contract is the product (ADR-0008 driver 4). The schema is
    `additionalProperties: false` at every level, so this catches a field this
    module invents as well as one it forgets."""
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = _REPO / "contracts" / "schemas" / "prompt_templates_response.json"
    if not schema_path.is_file():  # pragma: no cover - contract not yet landed
        pytest.skip(f"{schema_path} not present in this checkout")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    templates = load_templates(_write(tmp_path, [_text_record(), _ppi_record()]))
    body = {"templates": [to_wire(t) for t in templates.values()]}
    jsonschema.validate(body, schema)


# ---------------------------------------------------------------------------
# A realistic template, end to end
# ---------------------------------------------------------------------------


def _ppi_record() -> dict[str, Any]:
    """The literature console's PPI extraction prompt, moved server-side.

    Ported from `frontend/src/literature/extraction.ts::buildPrompt` (table
    branch) — the prompt the browser builds today and posts to BV-BRC Copilot.
    Its three optional clauses are exactly the `withGenes` / `withOther`
    string-concatenation the TS does, expressed as conditional sections; that
    correspondence is the point of this test, since ADR-0008 exists to let this
    specific prompt stop being assembled in a browser.
    """
    return {
        "id": "ppi-extraction",
        "version": 1,
        "label": "Protein-Protein Interaction (PPI)",
        "output": "table",
        "columns": [
            "Pathogen",
            "Protein A",
            "Protein B",
            "Interaction Type",
            "Method",
            "Assertion",
            "Reference",
        ],
        "slots": [
            {"name": "organism", "required": True, "max_len": 120, "label": "Organism"},
            {"name": "genes", "required": False, "max_len": 500, "label": "Genes/proteins"},
            {"name": "other_terms", "required": False, "max_len": 500, "label": "Other terms"},
        ],
        "system": (
            "You extract structured assertions from biomedical literature. Use ONLY the "
            "provided passages and never invent an entry."
        ),
        "user": (
            "Based on the literature context below, extract structured data about "
            '{{label}} for organism "{{organism}}"'
            "{{#genes}} involving genes/proteins: {{genes}}{{/genes}}"
            "{{#other_terms}}, related to: {{other_terms}}{{/other_terms}}.\n\n"
            "Return ONLY a TSV (tab-separated values) table with these columns:\n"
            "{{columns}}\n\n"
            "Rules:\n"
            "- Output the header row first, then data rows.\n"
            "- Use tab characters to separate columns.\n"
            '- If a value is unknown, use "N/A".\n'
            "- Include the reference (author, year, DOI if available) for each row.\n"
            "- Do NOT include any explanatory text before or after the table.\n"
            "- Extract as many relevant entries as the literature supports.\n\n"
            "--- LITERATURE CONTEXT ---\n\n"
            "{{context}}"
        ),
    }


_PASSAGES = (
    "[1] Source 1 (Yersinia T3SS interactome) [DOI: 10.1/abc]:\n"
    "YopH binds SKAP-HOM in infected macrophages."
)


def test_ppi_template_renders_the_full_prompt(tmp_path: Path) -> None:
    t = load_templates(_write(tmp_path, [_ppi_record()]))["ppi-extraction"]
    system, user = render(
        t,
        {"organism": "Yersinia pestis", "genes": "yopH, yopE"},
        _PASSAGES,
    )
    assert system.startswith("You extract structured assertions")
    assert "{{" not in system
    assert user.startswith(
        "Based on the literature context below, extract structured data about "
        'Protein-Protein Interaction (PPI) for organism "Yersinia pestis"'
        " involving genes/proteins: yopH, yopE.\n\n"
    )
    # {{columns}} becomes the TSV header the model is asked to emit.
    assert "Pathogen\tProtein A\tProtein B\tInteraction Type\tMethod\tAssertion\tReference" in user
    # The omitted optional slot takes its whole clause with it — no dangling
    # ", related to: " with nothing after it.
    assert ", related to:" not in user
    assert user.endswith(_PASSAGES)
    # Nothing unresolved reached the model.
    assert "{{" not in user


def test_ppi_template_requires_its_organism(tmp_path: Path) -> None:
    t = load_templates(_write(tmp_path, [_ppi_record()]))["ppi-extraction"]
    with pytest.raises(TemplateRenderError) as exc:
        render(t, {"genes": "yopH"}, _PASSAGES)
    assert exc.value.slot == "organism"


def test_ppi_template_caps_a_runaway_gene_list(tmp_path: Path) -> None:
    t = load_templates(_write(tmp_path, [_ppi_record()]))["ppi-extraction"]
    with pytest.raises(TemplateRenderError, match="over its declared max_len"):
        render(t, {"organism": "Yersinia pestis", "genes": "x" * 501}, _PASSAGES)


def test_ppi_template_does_not_let_a_value_hijack_the_instructions(tmp_path: Path) -> None:
    """The bound ADR-0008 actually claims: a value cannot become *syntax*. It
    says nothing about a value that reads as an instruction — the ADR's "does
    NOT do" section is explicit that injection is out of scope, and this test
    documents the line rather than pretending it is further out than it is."""
    t = load_templates(_write(tmp_path, [_ppi_record()]))["ppi-extraction"]
    _, user = render(
        t,
        {"organism": "Yersinia pestis", "genes": "{{context}}{{#other_terms}}"},
        _PASSAGES,
    )
    assert "involving genes/proteins: {{context}}{{#other_terms}}." in user
    assert user.count(_PASSAGES) == 1


def test_the_shipped_example_file_loads_and_renders() -> None:
    """The example in contracts/fixtures is executable documentation.

    It is what an operator copies to start from and what phase 3 points the
    literature console at, so a typo in it is a broken boot for whoever follows
    the docs. Loading it here means the example cannot rot: every validation rule
    this module enforces is enforced against the file we ship.
    """
    from pathlib import Path

    example = Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "prompt-templates.example.yaml"
    templates = load_templates(example)

    assert set(templates) == {"ppi-extraction", "protein-function", "mutation", "literature-summary"}

    ppi = templates["ppi-extraction"]
    assert ppi.output == "table"
    assert ppi.columns is not None and ppi.columns[0] == "Pathogen"

    system, user = render(ppi, {"organism": "SARS-CoV-2", "genes": "Spike, ACE2"}, "<<PASSAGES>>")
    assert "{{" not in system and "{{" not in user, "an unrendered marker reached the model"
    assert "<<PASSAGES>>" in user
    assert "involving genes/proteins: Spike, ACE2" in user
    # other_terms was not supplied, so its section renders away rather than
    # leaving a dangling ", related to:" stub.
    assert "related to:" not in user

    # The prose template carries no columns, so a client renders the answer as
    # text instead of trying to parse a table out of it.
    assert templates["literature-summary"].output == "text"
    assert templates["literature-summary"].columns is None

    # docs/API.md shows a worked example with this hash in it. Pin it here so a
    # content change to the shipped file cannot leave the documentation quoting
    # a value the loader no longer produces — which is exactly what happened
    # when `label` moved into the hash.
    docs = (Path(__file__).resolve().parents[2] / "docs" / "API.md").read_text()
    assert ppi.hash in docs, (
        f"docs/API.md quotes a stale hash; the shipped ppi-extraction is now {ppi.hash}"
    )


def test_a_column_containing_a_tab_is_refused(tmp_path: Path) -> None:
    """`{{columns}}` renders tab-joined, so an embedded tab makes N declared
    columns into N+1 prompt fields and mis-aligns every row a client parses
    against the declaration. Caught at load, like the other authoring errors."""
    with pytest.raises(TemplateValidationError, match="contains a tab"):
        _load_one(tmp_path, output="table", columns=["A\tB", "C"], user="{{context}}{{focus}}{{columns}}")


def test_duplicate_column_names_are_refused(tmp_path: Path) -> None:
    with pytest.raises(TemplateValidationError, match="unique"):
        _load_one(tmp_path, output="table", columns=["A", "A"], user="{{context}}{{focus}}{{columns}}")


@pytest.mark.parametrize("bad", ["My Template", "a/b", "PPI", "a" * 65, "-lead"])
def test_ids_outside_the_published_pattern_are_refused(tmp_path: Path, bad: str) -> None:
    """The listing's `id` is bounded by contract because it is caller-supplied
    and reaches logs. Accepting a wider one here let the BOOT succeed and then
    made GET /v1/prompt-templates violate its own schema at runtime — inverting
    ADR-0008 rule 5, which exists so an authoring error fails loudly at load.

    The previous test for this parametrized only "", "  ", 7, None and " padded"
    — none of which exercise the pattern."""
    with pytest.raises(TemplateValidationError, match="does not match"):
        _load_one(tmp_path, id=bad)


def test_a_table_template_may_raise_its_own_token_ceiling(tmp_path: Path) -> None:
    """`max_output_tokens` exists because the 512-token default silently ate rows.

    An extraction template asks for as many entries as the literature supports
    and then loses the ones that do not fit — with nothing in the text to say so,
    which made the row count track how verbose the model happened to be rather
    than what the corpus contained.
    """
    t = _load_one(tmp_path, max_output_tokens=2500)
    assert t.max_output_tokens == 2500
    assert _load_one(tmp_path).max_output_tokens is None


@pytest.mark.parametrize("bad", [0, -1, "2500", 12.5, True])
def test_a_bad_token_ceiling_is_refused_at_load(tmp_path: Path, bad: object) -> None:
    with pytest.raises(TemplateValidationError, match="max_output_tokens"):
        _load_one(tmp_path, max_output_tokens=bad)


def test_the_token_ceiling_is_content(tmp_path: Path) -> None:
    """It changes how much of the answer survives, so two tenants whose templates
    differ only here can produce materially different tables from one corpus."""
    assert _load_one(tmp_path).hash != _load_one(tmp_path, max_output_tokens=2500).hash


def test_the_shipped_table_templates_all_raise_the_ceiling(tmp_path: Path) -> None:
    """Regression for the defect that motivated the field.

    Every TABLE template we ship must raise its own ceiling. At the 512-token
    server default a table is bounded by TOTAL output, so the number of rows
    that survive tracks how verbose the model happens to be per row — measured
    against a live model on 2026-09-15, the same prompt produced 12 rows ending
    mid-reference at 512 (finish_reason "length") and 12 complete rows at 2500
    ("stop"), while a wordier draw of the same template returned only 2. A prose
    template does not need it and deliberately does not declare one.
    """
    from pathlib import Path as _P

    example = _P(__file__).resolve().parents[2] / "contracts" / "fixtures" / "prompt-templates.example.yaml"
    templates = load_templates(example)
    for t in templates.values():
        if t.output == "table":
            assert t.max_output_tokens and t.max_output_tokens >= 2000, (
                f"{t.id} is a table template with no meaningful ceiling"
            )
        else:
            assert t.max_output_tokens is None


def test_an_absurd_token_ceiling_is_refused_at_load(tmp_path: Path) -> None:
    """A fat-fingered extra zero must fail where it was typed.

    Unbounded, it reaches the model server verbatim, comes back a 400, and this
    app degrades that into a generic "[answer generation failed]" — the fault
    reported nowhere near where it was made.
    """
    with pytest.raises(TemplateValidationError, match="sanity ceiling"):
        _load_one(tmp_path, max_output_tokens=25_000_000)


def test_a_template_without_a_ceiling_keeps_its_hash(tmp_path: Path) -> None:
    """`max_output_tokens` is omitted from the hash payload when unset.

    Including the key unconditionally moved the hash of every template that does
    not declare one — the shipped `literature-summary` changed while its bytes
    were untouched and it stayed v1, so two tenants either side of the upgrade
    would serve the same (id, version) with different hashes. That is the drift
    detector firing where nothing drifted.
    """
    import json as _json
    from pathlib import Path as _P

    from ragstack.prompts import content_hash

    kw = {
        "version": 1,
        "label": "L",
        "output": "text",
        "columns": None,
        "slots": (),
        "system": "s",
        "user": "u",
    }
    # The assertion that actually BITES. Comparing content_hash(no kwarg) with
    # content_hash(max_output_tokens=None) is vacuous: both take the SAME branch
    # in the fixed code AND in the broken one, so it cannot see the defect. A
    # review proved it — restoring the unconditional payload key left all 3888
    # tests green while reverting literature-summary's hash to e1f00e1ba9444840.
    #
    # Pin the SHIPPED value instead. It is the thing that must not move: a tenant
    # that recorded (literature-summary, v1, b6a671b3a40015cf) as an experimental
    # condition has to still match after an upgrade that did not touch its bytes.
    example = _P(__file__).resolve().parents[2] / "contracts" / "fixtures" / "prompt-templates.example.yaml"
    assert load_templates(example)["literature-summary"].hash == "b6a671b3a40015cf", (
        "a template that declares no ceiling changed hash — the drift detector "
        "is firing where nothing drifted"
    )
    # And the key is genuinely absent from the hashed payload, not merely null.
    from ragstack.prompts import _canonical_payload_json

    payload = {"version": 1, "label": "L", "output": "text", "columns": None,
               "system": "s", "user": "u", "slots": []}
    assert "max_output_tokens" not in _json.loads(
        _canonical_payload_json(payload)
    ), "an unset ceiling reached the hash payload"
    assert content_hash(**kw) != content_hash(**kw, max_output_tokens=2500)
