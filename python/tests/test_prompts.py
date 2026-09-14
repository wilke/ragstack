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
    with pytest.raises(TemplateValidationError, match="'user'"):
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


def test_label_is_not_content(tmp_path: Path) -> None:
    """DECIDED: `label` is excluded from the hash, at both the template and the
    slot level. It is a string for a picker; two tenants that renamed a menu
    entry but kept identical bodies still produce identical prompts and are
    still comparable, and flagging them as drifted would be a false positive in
    the one signal that exists to catch real drift.

    Note the consequence, which is real: a label-only edit is invisible to the
    hash, so `version` alone carries it. ADR-0008 says version is bumped on any
    content change, and by this definition a label edit is not one."""
    base = _load_one(tmp_path)
    relabelled = _load_one(tmp_path, label="A different picker entry")
    assert base.label != relabelled.label
    assert base.hash == relabelled.hash


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
