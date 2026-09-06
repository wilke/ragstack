"""The grading contract's fixtures validate, and its two schema sources agree.

No server is involved: this file pins the CONTRACT itself (docs/plans/grading-ui.md
phase 1), so it runs green on a checkout where nothing implements grading yet,
while ``test_grading.py`` skips. Three claims:

* every fixture under ``contracts/fixtures/grading/`` validates against the
  JSON schema its filename names (``grading_verdict__reader_a.json`` → the
  ``grading_verdict`` schema; the ``__`` suffix distinguishes examples);
* the export fixture's CSV texts are what
  ``docs/plans/results/stage0/s0_rdev_score.py::read_verdicts`` accepts — the
  header, the vocabulary, no duplicate ``pair_id`` — re-stated here rather than
  imported, because importing that module drags in ``s0_common`` when it is
  importable, and ``s0_common`` creates the study's scratch tree on import;
* every ``Grading*`` component in ``contracts/openapi.yaml`` and every titled
  object in ``contracts/schemas/grading_*.json`` (top level and ``$defs``) name
  the same required fields, the same properties, the same enums, and
  ``additionalProperties: false`` — the same two-sources discipline
  ``python/tests/api/test_error_schema.py`` applies to ``Error``. The JSON files
  carry the long-form rationale; the OpenAPI components are what ``/docs``
  renders; a field added to one and not the other is the drift this catches.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import jsonschema
import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_CONTRACTS = _ROOT / "contracts"
_FIXTURES = _CONTRACTS / "fixtures" / "grading"

#: SPEC-confirmation-run.md §6.6.2, byte-for-byte ``s0_rdev_score.VERDICTS``.
VERDICTS = (
    "correct",
    "wrong-location",
    "non-minimal",
    "missed-evidence",
    "correctly-none",
    "ambiguous",
)


def _resolver(schemas: dict[str, dict]) -> jsonschema.RefResolver:
    store = {s.get("$id", name): s for name, s in schemas.items()}
    return jsonschema.RefResolver.from_schema({}, store=store)


def _fixture_files() -> list[Path]:
    files = sorted(_FIXTURES.glob("*.json"))
    assert files, f"no fixtures under {_FIXTURES}"
    return files


@pytest.mark.parametrize("path", _fixture_files(), ids=lambda p: p.name)
def test_fixture_validates_against_its_schema(path: Path, schemas: dict[str, dict]) -> None:
    schema_name = path.stem.split("__")[0]
    assert schema_name in schemas, (
        f"{path.name} names schema {schema_name!r}, which is not under contracts/schemas/"
    )
    jsonschema.validate(
        instance=json.loads(path.read_text(encoding="utf-8")),
        schema=schemas[schema_name],
        resolver=_resolver(schemas),
    )


# --------------------------------------------------------------------------- #
# The scorer's acceptance rules (s0_rdev_score.read_verdicts)
# --------------------------------------------------------------------------- #
def scorer_accepts(text: str) -> dict[str, dict]:
    """``s0_rdev_score.read_verdicts``, restated: header ``pair_id,verdict,notes``,
    a blank verdict is *not yet read*, a non-blank one must be in the vocabulary,
    no duplicate ``pair_id``. Returns what the scorer would."""
    rd = csv.DictReader(io.StringIO(text, newline=""))
    assert rd.fieldnames == ["pair_id", "verdict", "notes"], (
        f"header must be pair_id,verdict,notes (got {rd.fieldnames})"
    )
    rows: dict[str, dict] = {}
    for i, r in enumerate(rd, 2):
        pid = (r.get("pair_id") or "").strip()
        assert pid, f"line {i}: empty pair_id"
        v = (r.get("verdict") or "").strip().lower()
        assert not v or v in VERDICTS, f"line {i}: verdict {v!r} is not in {VERDICTS}"
        assert pid not in rows, f"line {i}: duplicate pair_id {pid!r}"
        rows[pid] = {"verdict": v or None, "notes": (r.get("notes") or "").strip()}
    return rows


def test_export_csvs_are_what_the_scorer_reads() -> None:
    export = json.loads((_FIXTURES / "grading_export_response.json").read_text())
    create = json.loads((_FIXTURES / "grading_batch_create_request.json").read_text())
    batch_order = [t["pair_id"] for t in create["tasks"]]

    labels = [c["label"] for c in export["csv"]]
    assert labels == [r["label"] for r in export["readers"]] + ["ADJ"], labels
    for entry in export["csv"]:
        assert entry["filename"] == f"rdev_verdicts_{entry['label']}.csv"
        assert entry["content"].endswith("\n") and "\r" not in entry["content"]
        rows = scorer_accepts(entry["content"])
        assert list(rows) == batch_order, (
            f"{entry['filename']}: rows must be every task in batch order, got {list(rows)}"
        )

    # The CSV cells are the JSON rows' values verbatim.
    by_label = {c["label"]: scorer_accepts(c["content"]) for c in export["csv"]}
    for v in export["verdicts"]:
        cell = by_label[v["label"]][v["pair_id"]]
        assert cell == {"verdict": v["verdict"], "notes": v["notes"]}, (v, cell)
    for a in export["adjudications"]:
        cell = by_label["ADJ"][a["pair_id"]]
        assert cell == {"verdict": a["verdict"], "notes": a["notes"]}, (a, cell)
    adjudicated = {a["pair_id"] for a in export["adjudications"]}
    for pid, cell in by_label["ADJ"].items():
        if pid not in adjudicated:
            assert cell == {"verdict": None, "notes": ""}, (
                f"{pid}: an unadjudicated task must be a blank cell, got {cell}"
            )


def test_fixture_reader_orders_follow_the_seed_rule() -> None:
    """The listing fixture is reader A's, so its order must be the contract's
    ``random.Random(order_seed + 1).shuffle`` of the create body's task order."""
    import random

    create = json.loads((_FIXTURES / "grading_batch_create_request.json").read_text())
    listing = json.loads((_FIXTURES / "grading_tasks_response.json").read_text())
    batch = json.loads((_FIXTURES / "grading_batch.json").read_text())
    assert listing["reader"] == batch["readers"][0]
    order = list(range(len(create["tasks"])))
    random.Random(batch["order_seed"] + 1).shuffle(order)
    expected = [create["tasks"][i]["pair_id"] for i in order]
    assert [t["pair_id"] for t in listing["tasks"]] == expected


# --------------------------------------------------------------------------- #
# openapi.yaml components ↔ contracts/schemas/grading_*.json
# --------------------------------------------------------------------------- #
def _titled_objects(schema: dict) -> dict[str, dict]:
    out = {schema["title"]: schema}
    for name, sub in schema.get("$defs", {}).items():
        assert sub.get("title") == name, f"$defs entry {name!r} must carry its own title"
        out[name] = sub
    return out


def _json_side() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted((_CONTRACTS / "schemas").glob("grading_*.json")):
        for title, obj in _titled_objects(json.loads(path.read_text())).items():
            assert title not in out, f"{title} is defined twice on the JSON side"
            out[title] = obj
    return out


def _openapi_side() -> dict[str, dict]:
    doc = yaml.safe_load((_CONTRACTS / "openapi.yaml").read_text())
    return {k: v for k, v in doc["components"]["schemas"].items() if k.startswith("Grading")}


def test_every_grading_component_exists_on_both_sides() -> None:
    j, o = set(_json_side()), set(_openapi_side())
    assert j == o, f"only in JSON: {sorted(j - o)}; only in openapi.yaml: {sorted(o - j)}"


@pytest.mark.parametrize("title", sorted(_json_side()))
def test_grading_component_matches_its_json_schema(title: str) -> None:
    js, oa = _json_side()[title], _openapi_side()[title]
    assert js.get("additionalProperties") is False, f"{title}: JSON side must forbid extras"
    assert oa.get("additionalProperties") is False, f"{title}: openapi side must forbid extras"
    assert set(js.get("required", [])) == set(oa.get("required", [])), (
        f"{title}: required differs — json {sorted(js.get('required', []))}, "
        f"openapi {sorted(oa.get('required', []))}"
    )
    assert set(js["properties"]) == set(oa["properties"]), (
        f"{title}: properties differ — json {sorted(js['properties'])}, "
        f"openapi {sorted(oa['properties'])}"
    )
    for name, jp in js["properties"].items():
        op = oa["properties"][name]
        if "enum" in jp or "enum" in op:
            assert jp.get("enum") == op.get("enum"), f"{title}.{name}: enum differs"
        if "pattern" in jp or "pattern" in op:
            assert jp.get("pattern") == op.get("pattern"), f"{title}.{name}: pattern differs"
        if "type" in jp and "type" in op:
            assert jp["type"] == op["type"], f"{title}.{name}: type differs"


def test_grading_paths_reference_only_grading_components() -> None:
    """Every `/v1/grading/*` operation's request/response schema is a Grading*
    component, and every Grading* component is reachable from one of them (or
    from another Grading* component) — no orphan definitions."""
    doc = yaml.safe_load((_CONTRACTS / "openapi.yaml").read_text())
    comps = _openapi_side()

    def refs_in(node: object) -> set[str]:
        found: set[str] = set()
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "$ref" and isinstance(v, str):
                    found.add(v.rsplit("/", 1)[-1])
                else:
                    found |= refs_in(v)
        elif isinstance(node, list):
            for v in node:
                found |= refs_in(v)
        return found

    from_paths: set[str] = set()
    for path, ops in doc["paths"].items():
        if path.startswith("/v1/grading/"):
            from_paths |= refs_in(ops)
    assert from_paths and all(r.startswith("Grading") for r in from_paths), from_paths

    reachable, frontier = set(), set(from_paths)
    while frontier:
        name = frontier.pop()
        reachable.add(name)
        frontier |= refs_in(comps[name]) - reachable
    assert reachable == set(comps), f"orphan components: {sorted(set(comps) - reachable)}"
