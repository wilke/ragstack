"""The grading importer: the two rules that decide whether a read is readable.

1. **Sentence renumbering must be accompanied by translation.** ``segment``
   (copied from ``s0_label.py``) keys a sentence by ``enumerate``'s ``k`` over
   all candidate spans and then drops the blank ones, so a unit containing a
   blank has a GAP — and ``s0_rdev.py``'s readsheet prints that gapped key, which
   is the space the r3.1 labels address. ``GradingDocument`` requires ``i`` to
   equal the array position. Renumbering without translating would slide every
   span in a gapped unit onto the wrong sentences: a highlight over text the
   labeler never claimed, which the reader would then grade.
2. **D3 rule 1's merge keeps the SMALLER set.** ``SPEC-confirmation-run.md``
   §6.2.1: the intersection-preserving union, "so merging can never make a unit
   easier to cover".

Plus one end-to-end check that the committed pilot package produces a body the
published schema accepts — the thing the importer is FOR.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "python" / "scripts" / "grading_import.py"
_PILOT = (
    _REPO / "docs" / "plans" / "results" / "stage0" / "artifacts"
    / "rdev-pilot-read" / "pilot_data_r3.json"
)
_SCHEMAS = _REPO / "contracts" / "schemas"


def _module():
    spec = importlib.util.spec_from_file_location("_grading_import", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gi = _module()


# --------------------------------------------------------------------------- #
# Renumbering and translation
# --------------------------------------------------------------------------- #
def test_a_gapped_unit_is_renumbered_and_the_labels_are_translated():
    """``segment``'s output with a hole at key 1 — a blank sentence it dropped."""
    seg = [
        (0, "Abstract", [(0, 0, 10, "First."), (2, 20, 30, "Third."), (3, 30, 40, "Fourth.")]),
        (1, "Results", [(0, 50, 60, "Result.")]),
    ]
    doc, unit_map, sentence_maps = gi._document_json("doc-1", "Title", seg)

    assert [u["index"] for u in doc["units"]] == [0, 1]
    assert [s["i"] for s in doc["units"][0]["sentences"]] == [0, 1, 2], (
        "the contract requires `i` to equal the position; the gap is closed"
    )
    assert [s["text"] for s in doc["units"][0]["sentences"]] == [
        "First.", "Third.", "Fourth."
    ]
    assert unit_map == {0: 0, 1: 1}
    assert sentence_maps[0] == {0: 0, 2: 1, 3: 2}

    # A label written against the readsheet's keys lands on the right sentences.
    span = {"unit": 0, "first_sentence": 2, "last_sentence": 3}
    assert gi._translate(span, unit_map, sentence_maps, "where") == (0, 1, 2)
    assert gi._span_text(doc, 0, 1, 2) == "Third. Fourth."


def test_a_label_naming_a_sentence_this_segmentation_dropped_is_refused():
    """Loudly, not silently: the labels and the segmentation disagreeing means
    the reader would be shown a highlight nobody claimed."""
    seg = [(0, "Abstract", [(0, 0, 10, "First."), (2, 20, 30, "Third.")])]
    _doc, unit_map, sentence_maps = gi._document_json("doc-1", "", seg)
    with pytest.raises(SystemExit, match="first_sentence 1 is not a sentence"):
        gi._translate(
            {"unit": 0, "first_sentence": 1, "last_sentence": 2}, unit_map, sentence_maps, "p"
        )
    with pytest.raises(SystemExit, match="names unit 5"):
        gi._translate(
            {"unit": 5, "first_sentence": 0, "last_sentence": 0}, unit_map, sentence_maps, "p"
        )


# --------------------------------------------------------------------------- #
# D3 rule 1
# --------------------------------------------------------------------------- #
def _spans(*pairs):
    return [{"start": a, "end": b, "unit": 0, "first_sentence": 0, "last_sentence": 0}
            for a, b in pairs]


def test_two_judges_naming_the_same_evidence_become_one_set_with_both_sources():
    merged = gi._merge_sets(
        [("scout", _spans((0, 100))), ("qwen", _spans((10, 100)))]
    )
    assert len(merged) == 1
    sources, spans = merged[0]
    assert sources == ["scout", "qwen"]
    assert gi._char_len(spans) == 90, (
        "the SMALLER set is retained as canonical (D3 rule 1's "
        "intersection-preserving union), so merging never makes a unit easier"
    )


def test_sets_below_the_jaccard_threshold_stay_separate():
    # 20 of 120 characters overlap: J = 20/100 < 0.5.
    merged = gi._merge_sets([("scout", _spans((0, 40))), ("qwen", _spans((20, 100)))])
    assert [s for s, _ in merged] == [["scout"], ["qwen"]]
    assert gi._jaccard(_spans((0, 40)), _spans((20, 100))) == pytest.approx(20 / 100)


def test_the_same_judge_repeating_a_set_across_presentations_collapses():
    """The ``--all-presentations`` case: five readings of one document must not
    become five evidence sets on the reader's screen."""
    merged = gi._merge_sets([("scout", _spans((0, 100)))] * 5)
    assert len(merged) == 1 and merged[0][0] == ["scout"]


def test_an_empty_set_contributes_nothing():
    """The labeler's 'no localizable evidence' verdict is an ABSENCE of sets,
    not a set with no spans — a task with no claims is legal and meaningful."""
    assert gi._merge_sets([("scout", [])]) == []


def test_sources_are_deduplicated_order_preserving():
    assert gi._unique(["scout", "scout", "qwen", "scout"]) == ["scout", "qwen"]


# --------------------------------------------------------------------------- #
# End to end: the committed pilot package
# --------------------------------------------------------------------------- #
def test_the_pilot_package_builds_a_body_the_schema_accepts(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    argv = [
        "pilot",
        "--pilot", str(_PILOT),
        "--readers", "@service:reader-a", "@service:reader-b",
        "-o", str(tmp_path / "batch.json"),
    ]
    assert gi.main(argv) == 0
    body = json.loads((tmp_path / "batch.json").read_text())

    store = {}
    for p in _SCHEMAS.glob("*.json"):
        s = json.loads(p.read_text())
        store[s.get("$id", p.name)] = s
    resolver = jsonschema.RefResolver.from_schema({}, store=store)
    jsonschema.validate(
        instance=body, schema=store["grading_batch_create_request.json"], resolver=resolver
    )

    assert len(body["tasks"]) == 10
    assert body["order_seed"] == gi.SEED_RDEV, (
        "the R-dev draw's seed, so the UI's per-reader order IS the readsheets'"
    )
    assert body["rubric_sha256"] == (
        "2e11f3688de916da8bfc8b5b0a788050bf9d077960d616d33490c6ecf747363b"
    ), "sha256 of docs/plans/results/design/RUBRIC-evidence.md"
    strata = {t["stratum"] for t in body["tasks"]}
    assert strata == {"model_positive", "model_negative", "deep_section", "long_document"}
    # Every span points into its own document — the create endpoint would 422
    # otherwise, and a reader would be shown a highlight that cannot render.
    assert all(
        sp["unit"] < len(t["document"]["units"])
        for t in body["tasks"]
        for c in t["claims"]
        for sp in c["spans"]
    )
