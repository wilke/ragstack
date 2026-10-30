"""Glossary benchmark round 2, set P only: the field-list drafting mode (pure half).

No network, no model, no store. :mod:`glossbench_r2_pool` plans the drafting
jobs with it, :mod:`glossbench_r2_checks` checks drafts with it and
:mod:`glossbench_r2_draft` exports to the papers-OC owner's format with it.

**The field list** (a private file, given as a path; never copied into the
repo): JSON (a list, or an object with a ``fields`` list; other top-level keys
are ignored) or JSONL, one field per entry::

    {"field": "occupant_count", "unit": "person", "definition": "...",
     "terms": ["occupants", ...],            # alias: "extraction_terms"
     "kind": "quantity" | "condition" | "constant",   # optional, default quantity
     "in_set": true | false,                 # optional
     "constant": true | false}               # optional

``n_terms``, when present, must equal ``len(terms)`` (a truncated term list is
refused: the file's own count is the check). Other keys are ignored. A field
named twice, an empty definition or an empty term list is refused.

**in_set** (does the field occur in the papers-OC owner's held set?) is
COMPUTED when the held set's records are given (``--in-set-records``: JSONL,
the field name under ``in_set_key``, default ``field``; only that key is read),
and the records' sha256 is recorded. Without them the file's own flag is used
(``null`` when absent).

**constant** is the union of the file's flag / ``kind`` and the configured
``constants`` list (default :data:`DEFAULT_CONSTANTS`). Every configured
constant must be in the file.

**Planning** (:func:`plan_strata`): conditions are excluded; only
``priority_fields`` are drafted when that list is set (a name outside the file
is refused); each planned field gets a weight — ``weight_new_ground`` (default
2) when ``in_set`` is false AND it is not a constant, ``weight_other`` (1)
otherwise; a constant is capped at ``constant_cap`` (default 2) jobs over the
set. Every eval paper targets the pool's per-paper stratum quotas
(``s1/s2/s3_per_paper`` of the chunk's stratum, per-set overrides applied: set
P takes more S3); a paper short of candidates in a stratum records the
shortfall, which moves only to other papers in the same stratum. Each
(paper, stratum) cell's jobs are spread over its fields by weight
(:func:`allocate`, a capped field's leftover to the others). The plan records
the per-paper x stratum x field counts, stated and realised.

**Candidate chunks per field** (:func:`prefilter`) are selected arm-blind by a
cheap lexical test on the chunk text alone: the chunk prints a number (digits
or a number word: room composition stated in prose stays eligible) AND either
one of the field's terms occurs in it (NFKC, casefold, whitespace collapsed, at
word boundaries) or, for a field with a dimension, it prints a number followed
by a unit of that dimension or a related one (:data:`RELATED`; :func:`units_in`). Recall-oriented on purpose:
layer 1 and the verifier decide what the chunk actually states.

**Units** (:func:`parse_unit`): a small dimension map (length, mass, time,
temperature; counts, fractions and percentages are dimensionless). Values are
recorded as printed and never converted: ``2 cm2`` is compatible with ``m2``.
:func:`unit_check` reads the value (a verbatim substring of the quote) and
returns ``ok``, ``related`` (a different dimension that a room quantity can
bridge, e.g. air changes per hour against a volume flow: a flag), ``unknown``
(a unit-like token outside the map: a flag), ``field_unknown`` (the field's own
unit is outside the map: a flag) or ``incompatible`` (a reject).
"""
from __future__ import annotations

import functools
import json
import math
import random
import re
import sys
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import _glossbench_r2 as g  # noqa: E402

DRAFT_MODES = ("open", "fields")
KINDS = ("quantity", "condition", "constant")
#: The default constants. Source: the field binding's context-free table, the fields
#: whose mode is ``any`` (a value that holds in every paper, not a paper's setting).
DEFAULT_CONSTANTS = ("gravity_m_s2", "air_density_kg_m3", "air_dynamic_viscosity_pa_s",
                     "particle_density_kg_m3")
#: field lists whose in_set split is known: the check applies without being configured, so
#: it cannot be skipped by leaving ``expect_in_set`` out (sha256 of the file -> split)
KNOWN_FIELD_LISTS: dict[str, dict[str, int]] = {
    "1c3778918c76ae206de0fb1b66e256da33faebf8840d6c49ca232552b2911dfa": {"true": 13, "false": 13},  # v2
}
FIELD_DEFAULTS: dict[str, Any] = {
    "path": None,               # the field list (private)
    "in_set_records": None,     # the held set's records (private); --in-set-records overrides
    "in_set_key": "field",
    "expect_in_set": None,      # optional {"true": n, "false": m}: refuse a different split
    "constants": list(DEFAULT_CONSTANTS),
    "constant_cap": 2,
    "priority_fields": None,    # null = every non-condition field
    "weight_new_ground": 2.0,   # in_set false AND not a constant
    "weight_other": 1.0,
    #: null = the per-paper stratum quotas (pool s1/s2/s3_per_paper, per-set overrides applied)
    #: give every paper's cells their targets; a number scales those cells to that total
    "target_jobs": None,
    "min_field_chunks": 3,      # eval-paper eligibility: candidate chunks per paper
}


# ------------------------------------------------------------------ the field list

@dataclass(frozen=True)
class FieldSpec:
    field: str
    unit: str
    definition: str
    terms: tuple[str, ...]
    kind: str = "quantity"
    in_set: bool | None = None
    constant: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"field": self.field, "unit": self.unit, "definition": self.definition, "terms": list(self.terms),
                "kind": self.kind, "in_set": self.in_set, "constant": self.constant}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> FieldSpec:
        return cls(d["field"], d["unit"], d["definition"], tuple(d["terms"]), d["kind"], d["in_set"], d["constant"])


def _entries(path: Path) -> list[Any]:
    raw = path.read_text(encoding="utf-8")
    try:
        obj = json.loads(raw)
    except ValueError:
        return [json.loads(x) for x in raw.splitlines() if x.strip()]
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict) and isinstance(obj.get("fields"), list):
        return obj["fields"]
    raise SystemExit(f"field list {path.name}: expected a list, an object with 'fields', or JSONL")


def read_field_list(path: str | Path) -> tuple[list[FieldSpec], str]:
    """(fields in file order, the file's sha256)."""
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"field list not found: {p.name}")
    out: list[FieldSpec] = []
    seen: set[str] = set()
    for i, e in enumerate(_entries(p), 1):
        where = f"field list {p.name} entry {i}"
        if not isinstance(e, dict):
            raise SystemExit(f"{where}: not an object")
        name = str(e.get("field") or "").strip()
        if not name:
            raise SystemExit(f"{where}: no field name")
        if name in seen:
            raise SystemExit(f"{where}: field {name!r} named twice")
        seen.add(name)
        terms = e.get("terms", e.get("extraction_terms"))
        if not isinstance(terms, list) or not terms or not all(isinstance(t, str) and t.strip() for t in terms):
            raise SystemExit(f"{where}: terms must be a non-empty list of strings")
        n_terms = e.get("n_terms")
        if n_terms is not None and (isinstance(n_terms, bool) or not isinstance(n_terms, int) or n_terms != len(terms)):
            raise SystemExit(f"{where}: n_terms {n_terms!r} differs from len(terms) {len(terms)}: "
                             "the term list is truncated or the count is stale; refused")
        definition = str(e.get("definition") or "").strip()
        if not definition:
            raise SystemExit(f"{where}: empty definition")
        kind = e.get("kind") or "quantity"
        if kind not in KINDS:
            raise SystemExit(f"{where}: kind {kind!r} not in {KINDS}")
        for flag in ("in_set", "constant"):
            if e.get(flag) is not None and not isinstance(e.get(flag), bool):
                raise SystemExit(f"{where}: {flag} must be a boolean")
        if e.get("constant") is False and kind == "constant":
            raise SystemExit(f"{where}: kind constant but constant false")
        out.append(FieldSpec(name, str(e.get("unit") or "").strip(), definition,
                             tuple(t.strip() for t in terms), kind, e.get("in_set"),
                             bool(e.get("constant")) or kind == "constant"))
    if not out:
        raise SystemExit(f"field list {p.name}: no fields")
    return out, g.file_sha(p)


def held_set_fields(path: str | Path, key: str) -> tuple[Counter[str], int, str]:
    """(records per field name, records without the key, sha256) of the held set's
    JSONL. Only ``key`` is read from each record."""
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"in-set records not found: {p.name}")
    cnt: Counter[str] = Counter()
    missing = 0
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            v = json.loads(line).get(key)
            if isinstance(v, str) and v.strip():
                cnt[v.strip()] += 1
            else:
                missing += 1
    return cnt, missing, g.file_sha(p)


def field_config(cc: Mapping[str, Any], set_: str) -> dict[str, Any] | None:
    """The set's field-mode config, merged with the defaults; ``None`` in open mode."""
    mode = cc.get("draft_mode", "open")
    if mode not in DRAFT_MODES:
        raise SystemExit(f"set {set_}: draft_mode {mode!r} not in {DRAFT_MODES}")
    if mode == "open":
        if cc.get("fields"):
            raise SystemExit(f"set {set_}: a fields block needs draft_mode fields")
        return None
    if set_ != "P":
        raise SystemExit(f"set {set_}: draft_mode fields is for set P only")
    given = cc.get("fields") or {}
    unknown = set(given) - set(FIELD_DEFAULTS)
    if unknown:
        raise SystemExit(f"corpora.{set_}.fields: unknown keys {sorted(unknown)}")
    fc = {**FIELD_DEFAULTS, **given}
    if not fc["path"]:
        raise SystemExit(f"corpora.{set_}.fields.path is required in draft_mode fields")
    if int(fc["constant_cap"]) < 0 or float(fc["weight_new_ground"]) <= 0 or float(fc["weight_other"]) <= 0:
        raise SystemExit("fields: constant_cap >= 0 and positive weights are required")
    return fc


def resolve_fields(fc: Mapping[str, Any], in_set_records: str | None = None) -> dict[str, Any]:
    """Read the field list, compute ``in_set``, apply the constants list, and check
    every configured name against the file. Returns ``{"fields": [FieldSpec],
    "sha256", "in_set": {...provenance}, "constants": [...], "new_ground": [...]}``."""
    fields, sha = read_field_list(fc["path"])
    names = {f.field for f in fields}
    consts = [str(c) for c in (fc.get("constants") or [])]
    outside = sorted(set(consts) - names)
    if outside:
        raise SystemExit(f"fields: configured constants not in the field list: {outside}")
    rec_path = in_set_records or fc.get("in_set_records")
    in_set_info: dict[str, Any] = {"source": "file flags", "records_sha256": None}
    if rec_path:
        cnt, missing, rsha = held_set_fields(rec_path, str(fc["in_set_key"]))
        fields = [FieldSpec(f.field, f.unit, f.definition, f.terms, f.kind, f.field in cnt, f.constant)
                  for f in fields]
        in_set_info = {"source": "held-set records", "records_sha256": rsha, "key": fc["in_set_key"],
                       "records": sum(cnt.values()) + missing, "records_without_key": missing,
                       "record_fields_outside_list": len(set(cnt) - names)}
    fields = [FieldSpec(f.field, f.unit, f.definition, f.terms, "constant" if f.field in consts else f.kind,
                        f.in_set, f.constant or f.field in consts) for f in fields]
    split = {"true": sum(f.in_set is True for f in fields), "false": sum(f.in_set is False for f in fields),
             "unknown": sum(f.in_set is None for f in fields)}
    in_set_info["counts"] = split
    exp = fc.get("expect_in_set")
    if exp is None:
        exp = KNOWN_FIELD_LISTS.get(sha)
    in_set_info["expected"] = exp
    if exp is not None and (int(exp.get("true", -1)) != split["true"] or int(exp.get("false", -1)) != split["false"]):
        raise SystemExit(f"fields: in_set split {split} differs from expect_in_set {exp}")
    return {"fields": fields, "sha256": sha, "in_set": in_set_info,
            "constants": sorted(f.field for f in fields if f.constant),
            "new_ground": sorted(f.field for f in fields if new_ground(f))}


def new_ground(f: FieldSpec) -> bool:
    return f.in_set is False and not f.constant


# ------------------------------------------------------------------ units

Dim = dict[str, int]
_L, _M, _T, _K = "L", "M", "T", "K"
_BASE: dict[str, Dim] = {
    "m": {_L: 1}, "g": {_M: 1}, "s": {_T: 1}, "L": {_L: 3}, "l": {_L: 3},
    "Pa": {_M: 1, _L: -1, _T: -2}, "J": {_M: 1, _L: 2, _T: -2}, "W": {_M: 1, _L: 2, _T: -3}, "Hz": {_T: -1},
}
_PREFIXES = ("k", "M", "h", "d", "c", "m", "µ", "μ", "u", "n")
_EXACT: dict[str, Dim] = {
    **{w: {_T: 1} for w in ("sec", "secs", "second", "seconds", "min", "mins", "minute", "minutes", "h", "hr", "hrs",
                            "hour", "hours", "d", "day", "days", "week", "weeks", "year", "years", "yr")},
    **{w: {_L: 3} for w in ("litre", "liter", "litres", "liters")},
    **{w: {_L: 1} for w in ("ft", "feet", "foot", "metre", "meter", "metres", "meters", "micron", "microns")},
    **{w: {_K: 1} for w in ("K", "°C", "℃", "°F")},
    **{w: {_T: -1} for w in ("ACH", "ach", "ACPH", "rpm")},
    "cfm": {_L: 3, _T: -1},
    **{w: {} for w in ("%", "‰", "ppm", "ppb", "percent", "fraction", "scalar", "dimensionless", "-", "count",
                       "person", "persons", "people", "occupant", "occupants", "individual", "individuals",
                       "quanta", "quantum", "copies", "copy", "particle", "particles", "droplet", "droplets",
                       "°", "deg", "degree", "degrees", "dB")},
}
#: different dimensions that a room quantity bridges (a rate per hour times a volume is a
#: flow): a value in one, asked in the other, is flagged, not rejected.
RELATED: frozenset[tuple[tuple[tuple[str, int], ...], tuple[tuple[str, int], ...]]] = frozenset({
    ((("L", 3), ("T", -1)), (("T", -1),)), ((("T", -1),), (("L", 3), ("T", -1))),
})
_FACTOR = re.compile(r"^([^\W\d_]+|°[CF]?|℃|%|‰|-)\^?([-−]?\d+)?$")
_ACH = re.compile(r"\bair\s+changes?\s*(?:per|/)\s*(?:hour|hr|h)\b", re.I)


def _unit_of(tok: str) -> Dim | None:
    if tok in _EXACT:
        return _EXACT[tok]
    if tok in _BASE:
        return _BASE[tok]
    if tok.casefold() in _EXACT and len(tok) > 2:
        return _EXACT[tok.casefold()]
    for p in _PREFIXES:
        if tok.startswith(p) and tok[len(p):] in _BASE:
            return _BASE[tok[len(p):]]
    return None


def _add(acc: Dim, d: Dim, k: int) -> None:
    for key, v in d.items():
        acc[key] = acc.get(key, 0) + v * k
        if acc[key] == 0:
            del acc[key]


def parse_unit(text: str) -> Dim | None:
    """The dimension of a unit expression (``{}`` = dimensionless), or ``None`` when a
    factor is outside the map. ``m3/h``, ``m3 h-1``, ``m³·h⁻¹``, ``kg/m3``, ``Pa*s``,
    ``Pa s``, ``1/s``, ``m3 per hour``, ``air changes per hour``."""
    t = unicodedata.normalize("NFKC", text).strip().rstrip(".,;:")
    t = _ACH.sub("ACH", t)
    t = re.sub(r"\bper\b", "/", t)
    t = re.sub(r"[*·⋅()]", " ", t)
    if not t.strip():
        return None
    acc: Dim = {}
    for i, part in enumerate(t.split("/")):
        toks = part.split()
        if not toks and i == 0:
            return None
        for tok in toks:
            if tok == "1":
                continue
            m = _FACTOR.match(tok)
            if not m:
                return None
            d = _unit_of(m.group(1))
            if d is None:
                return None
            exp = int(m.group(2).replace("−", "-")) if m.group(2) else 1
            _add(acc, d, -exp if i else exp)
    return acc


def dim_key(d: Dim | None) -> tuple[tuple[str, int], ...] | None:
    return None if d is None else tuple(sorted(d.items()))


_NUM = re.compile(r"(?<![\w.])[-−+]?\d+(?:[.,]\d+)*(?:\s*(?:[×x]\s*10\s*\^?\s*[-−]?\d+|[eE][-−+]?\d+))?")
_NUM_WORD = re.compile(r"\b(?:" + "|".join(sorted(g.NUMBER_WORDS, key=len, reverse=True))
                       + r")(?:-(?:" + "|".join(sorted(g.NUMBER_WORDS, key=len, reverse=True)) + r"))?\b", re.I)
_UNIT_LIKE = re.compile(r"^[^\W\d_°%‰][\w°%‰/^\-−]*$|^[°%‰]")


def _unit_like(tok: str) -> bool:
    t = tok.rstrip(".,;:")
    if not t or t.casefold() in g.STOP_WORDS or t.casefold() in g.NUMBER_WORDS:
        return False
    return bool(_UNIT_LIKE.match(t)) and (len(t) <= 5 or "/" in t or any(c.isdigit() for c in t))


def units_in(text: str) -> list[tuple[str, str, Dim | None, bool]]:
    """Every number printed in ``text`` with the unit that follows it:
    ``(number, unit text, dimension, unit_like)``. The longest run of up to four
    following tokens that parses wins; none parses -> dimension ``None`` with
    ``unit_like`` telling a unit outside the map from an ordinary word."""
    t = unicodedata.normalize("NFKC", text)
    out = []
    spans = sorted([(m.start(), m.end()) for m in _NUM.finditer(t)] + [(m.start(), m.end()) for m in _NUM_WORD.finditer(t)])
    for s, e in spans:
        rest = re.split(r"[,;\[\]]|\s(?:and|or|to|vs)\s", t[e:e + 48], maxsplit=1)[0]
        toks = rest.split()                              # "2cm2": a glued unit is the first token
        found: tuple[str, Dim | None] = ("", {})
        for j in range(min(4, len(toks)), 0, -1):
            cand = " ".join(toks[:j])
            d = parse_unit(cand)
            if d is not None:
                found = (cand, d)
                break
        else:
            if toks and _unit_like(toks[0]):
                found = (toks[0], None)
        out.append((t[s:e], found[0], found[1], found[1] is None))
    return out


def unit_check(value: str, field_unit: str) -> str:
    """``ok`` | ``related`` | ``unknown`` | ``field_unknown`` | ``incompatible``
    (see the module docstring). A value with no number is ``incompatible`` here;
    layer 1 reports that as ``quantity_not_in_quote`` as well."""
    fd = dim_key(parse_unit(field_unit) if field_unit else {})
    if fd is None:
        return "field_unknown"
    seen = {"ok": False, "related": False, "unknown": False}
    for _, _, d, _ in units_in(value):
        k = dim_key(d)
        if k is None:
            seen["unknown"] = True
        elif k == fd:
            seen["ok"] = True
        elif (k, fd) in RELATED:
            seen["related"] = True
    for verdict in ("ok", "related", "unknown"):
        if seen[verdict]:
            return verdict
    return "incompatible"


# ------------------------------------------------------------------ the prefilter

def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


@functools.lru_cache(maxsize=4096)
def term_pattern(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(_norm(term)) + r"(?![a-z0-9])")


def candidate_fields(text: str, fields: Sequence[FieldSpec]) -> list[str]:
    """The fields whose prefilter the chunk passes: a printed number AND (a term at
    word boundaries OR, for a field with a dimension, a number followed by a unit of
    that dimension or a related one, e.g. air changes per hour for a volume flow).
    Arm-blind: the chunk text only."""
    if not g.has_number(text):
        return []
    n = _norm(text)
    dims = {dim_key(d) for _, _, d, _ in units_in(text) if d}
    out = []
    for f in fields:
        if any(term_pattern(t).search(n) for t in f.terms):
            out.append(f.field)
            continue
        fd = dim_key(parse_unit(f.unit) if f.unit else {})
        if fd and any(d == fd or (d, fd) in RELATED for d in dims):
            out.append(f.field)
    return out


def prefilter(text: str, f: FieldSpec) -> bool:
    return bool(candidate_fields(text, [f]))


# ------------------------------------------------------------------ quotas

def largest_remainder(total: int, weights: Mapping[str, float]) -> dict[str, int]:
    """Integer shares of ``total`` proportional to ``weights``; remainders by size, ties by name."""
    if total <= 0 or not weights:
        return dict.fromkeys(weights, 0)
    w = sum(weights.values())
    exact = {k: total * v / w for k, v in weights.items()}
    out = {k: math.floor(x) for k, x in exact.items()}
    left = total - sum(out.values())
    for k in sorted(exact, key=lambda k: (-(exact[k] - out[k]), k))[:left]:
        out[k] += 1
    return out


def allocate(total: int, weights: Mapping[str, float], caps: Mapping[str, int]) -> dict[str, dict[str, int]]:
    """Per field ``{"stated": share of total by weight, "quota": after caps and
    redistribution, "cap": cap}``. What a capped field cannot take goes to the
    fields with room, by weight, until the total is placed or nobody has room."""
    stated = largest_remainder(total, weights)
    quota = dict.fromkeys(weights, 0)
    active = [k for k in weights if caps[k] > 0]
    while active:
        left = total - sum(quota.values())
        if left <= 0:
            break
        share = largest_remainder(left, {k: weights[k] for k in active})
        moved = 0
        for k in active:
            give = min(share[k], caps[k] - quota[k])
            quota[k] += give
            moved += give
        active = [k for k in active if quota[k] < caps[k]]
        if moved == 0:
            break
    return {k: {"stated": stated[k], "quota": quota[k], "cap": caps[k]} for k in weights}


def planned_names(fields: Sequence[FieldSpec], fc: Mapping[str, Any]) -> list[str]:
    """The fields that may be drafted: no condition; only ``priority_fields`` when set
    (a name outside the field list, or a condition, is refused)."""
    by = {f.field: f for f in fields}
    pri = fc.get("priority_fields")
    if pri is not None:
        outside = sorted(set(pri) - set(by))
        if outside:
            raise SystemExit(f"fields: priority_fields not in the field list: {outside}")
        conds = sorted(p for p in pri if by[p].kind == "condition")
        if conds:
            raise SystemExit(f"fields: priority_fields names conditions (excluded): {conds}")
    return [f.field for f in fields if f.kind != "condition" and (pri is None or f.field in pri)]


STRATA = ("S1", "S2", "S3")


def cell_targets(papers: Sequence[str], per_paper: Mapping[str, int], total: int | None = None) -> dict[
        tuple[str, str], int]:
    """(paper, stratum) -> target jobs: ``per_paper`` (``{"S1": n1, "S2": n2, "S3": n3}``)
    for every paper; with ``total`` (``target_jobs``, or a pilot's N) the same cell
    weights scaled to that total by largest remainder."""
    cells = {(p, s): int(per_paper[s]) for p in sorted(papers) for s in STRATA}
    if total is None:
        return cells
    keyed = {f"{p}\x00{s}": float(v) for (p, s), v in cells.items() if v > 0}
    shares = largest_remainder(int(total), keyed) if keyed else {}
    return {c: shares.get(f"{c[0]}\x00{c[1]}", 0) for c in cells}


def plan_strata(fields: Sequence[FieldSpec], fc: Mapping[str, Any], rows: Sequence[Mapping[str, Any]],
                targets: Mapping[tuple[str, str], int], seed: str) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """The per-paper x stratum x field plan and the jobs (chunk id -> fields).

    ``rows`` are pool rows (``chunk_id``, ``paper``, ``stratum`` — the chunk's —,
    ``field_candidates``). One job = one (chunk, field) pair; a chunk may serve
    several fields. Steps:

    1. **Per stratum, per paper:** each (paper, stratum) cell is assigned
       ``min(target, capacity)``, capacity = its candidate pairs (a constant field
       counted at most ``constant_cap``). A cell's shortfall is recorded and moved
       only to cells of the SAME stratum with room (papers in a seeded order
       ``<seed>|strata|<stratum>``, one job at a time); what no cell can take is
       recorded as ``unplaced``.
    2. **Per cell, across fields:** the cell's jobs are spread over the fields with
       candidates there by the field weights (:func:`allocate`: largest remainder,
       a capped field's leftover to the others), each field capped by its candidate
       chunks in the cell and a constant by what is left of its ``constant_cap``
       over the whole set (cells in seeded paper order). Chunks per (cell, field) in
       a seeded order ``<seed>|fields|<field>|<paper>|<stratum>``.

    The plan records, per cell, the target, the assignment after redistribution,
    the shortfall, the realised count and the per-field stated share and realised
    count; per stratum and per field the totals."""
    by = {f.field: f for f in fields}
    names = planned_names(fields, fc)
    weights = {n: float(fc["weight_new_ground"]) if new_ground(by[n]) else float(fc["weight_other"]) for n in names}
    ccap = int(fc["constant_cap"])
    cand: dict[tuple[str, str], dict[str, list[str]]] = {}
    for r in rows:
        for f in r.get("field_candidates") or []:
            if f in weights and (r["paper"], r["stratum"]) in targets:
                cand.setdefault((r["paper"], r["stratum"]), {}).setdefault(f, []).append(r["chunk_id"])

    def capacity(c: tuple[str, str]) -> int:
        return sum(min(len(v), ccap) if by[f].constant else len(v) for f, v in cand.get(c, {}).items())

    assigned: dict[tuple[str, str], int] = {}
    shortfall: dict[tuple[str, str], int] = {}
    strata: dict[str, dict[str, int]] = {}
    for s in STRATA:
        cells = sorted(c for c in targets if c[1] == s)
        for c in cells:
            assigned[c] = min(int(targets[c]), capacity(c))
            shortfall[c] = int(targets[c]) - assigned[c]
        left = sum(shortfall[c] for c in cells)
        order = sorted(c[0] for c in cells)
        random.Random(f"{seed}|strata|{s}").shuffle(order)
        while left > 0:
            moved = False
            for p in order:
                c = (p, s)
                if left > 0 and assigned[c] < capacity(c):
                    assigned[c] += 1
                    left -= 1
                    moved = True
            if not moved:
                break
        strata[s] = {"target": sum(int(targets[c]) for c in cells), "shortfall": sum(shortfall[c] for c in cells),
                     "unplaced": left}

    papers = sorted({c[0] for c in targets})
    random.Random(f"{seed}|cells").shuffle(papers)
    used: Counter[str] = Counter()
    jobs: dict[str, list[str]] = {}
    cell_rec: dict[str, dict[str, Any]] = {}
    for p in papers:
        for s in STRATA:
            c = (p, s)
            if c not in targets:
                continue
            here = cand.get(c, {})
            caps = {f: (min(len(v), ccap - used[f]) if by[f].constant else len(v)) for f, v in here.items()}
            alloc = allocate(assigned[c], {f: weights[f] for f in here}, caps) if here else {}
            got: dict[str, int] = {}
            for f in sorted(alloc):
                q = alloc[f]["quota"]
                if q <= 0:
                    continue
                ids = sorted(here[f])
                random.Random(f"{seed}|fields|{f}|{p}|{s}").shuffle(ids)
                for cid in ids[:q]:
                    jobs.setdefault(cid, []).append(f)
                got[f] = q
                if by[f].constant:
                    used[f] += q
            cell_rec.setdefault(p, {})[s] = {
                "target": int(targets[c]), "assigned": assigned[c], "shortfall": shortfall[c],
                "realised": sum(got.values()), "fields": dict(sorted(got.items())),
                "stated": {f: a["stated"] for f, a in sorted(alloc.items())}}
    for s in STRATA:
        strata[s]["realised"] = sum(cell_rec[p][s]["realised"] for p in cell_rec if s in cell_rec[p])
    per_field = {n: {"weight": weights[n], "in_set": by[n].in_set, "constant": by[n].constant,
                     "new_ground": new_ground(by[n]),
                     "candidates": sum(len(v.get(n, ())) for v in cand.values()),
                     "realised": sum(cell_rec[p][s]["fields"].get(n, 0) for p in cell_rec for s in cell_rec[p])}
                 for n in names}
    plan = {"weights": {"new_ground": float(fc["weight_new_ground"]), "other": float(fc["weight_other"])},
            "constant_cap": ccap, "total_target": sum(int(v) for v in targets.values()),
            "realised": sum(strata[s]["realised"] for s in STRATA), "strata": strata,
            "excluded_conditions": sorted(f.field for f in fields if f.kind == "condition"),
            "not_prioritised": sorted(f.field for f in fields if f.kind != "condition" and f.field not in names),
            "fields": per_field, "cells": {p: cell_rec[p] for p in sorted(cell_rec)}}
    return plan, {k: sorted(v) for k, v in jobs.items()}


def anonymised_cells(plan: Mapping[str, Any]) -> dict[str, Any]:
    """The plan's per-paper cells keyed by rank (``p01``...), for a counts-only manifest."""
    return {f"p{i:02d}": v for i, (_, v) in enumerate(sorted(plan["cells"].items()), 1)}


# ------------------------------------------------------------------ the term-leak guard

#: Question words that say nothing about the quantity (removed before the comparison).
QUESTION_WORDS = frozenset("what how many much which value values give given reported report stated state "
                           "paper study used use".split())


def term_leak(question: str, terms: Iterable[str], definition: str, case: str = "",
              frequent: Iterable[str] = ()) -> tuple[float, int]:
    """(share, n): of the question's own content words — stemmed like the wording
    proxy, stop and question words removed, the definition's words (which the
    drafter is told to use), the stated case's words and the paper's frequent
    tokens removed — the share that comes from the term list; n = how many such
    words. 0.0 when none."""
    import wording_proxy as wp

    def ws(text: str) -> set[str]:
        return {wp.stem(t) for t in re.findall(r"[a-z0-9]+", wp.norm_definition(text))
                if len(t) > 1 and t not in g.STOP_WORDS and t not in QUESTION_WORDS}

    excused = ws(definition) | ws(case) | {wp.stem(t) for t in frequent}
    q = ws(question) - excused
    if not q:
        return 0.0, 0
    tv: set[str] = set()
    for t in terms:
        tv |= ws(t)
    return len(q & tv) / len(q), len(q)


_SYMBOLISH = set("_{}^=≈")


def abbreviation_like(term: str) -> bool:
    """A term that is a symbol or an abbreviation, not words: at most 6 characters and
    mostly upper case (``ACH``), or a single token of at most 4 letters (the term
    lists also print abbreviations in lower case: ``ach``, ``hepa``, ``qb``), or it
    contains a digit, ``_ { } ^ = ≈``, a Greek letter or another non-ASCII symbol."""
    t = unicodedata.normalize("NFKC", term).strip()
    if not t:
        return False
    letters = [c for c in t if c.isalpha()]
    if len(t) <= 6 and letters and sum(c.isupper() for c in letters) * 2 > len(letters):
        return True
    if " " not in t and t.isalpha() and t.isascii() and len(t) <= 4:
        return True
    return any(c.isdigit() or c in _SYMBOLISH or not c.isascii() for c in t)


#: punctuation stripped from a token's ends; brackets included, so a parenthesised
#: abbreviation ("air exchange rate (ACH)") is the token ``ach``, not ``(ach)``
_TOKEN_EDGE = ".,;:!?\"'()[]"


def _q_tokens(text: str) -> list[str]:
    return [w.strip(_TOKEN_EDGE) for w in unicodedata.normalize("NFKC", text).casefold().split()]


def abbreviation_in_question(question: str, terms: Iterable[str]) -> list[str]:
    """The abbreviation-like terms (:func:`abbreviation_like`) the question contains as
    whole tokens (NFKC, casefold, edge punctuation and brackets stripped; a multi-token
    term as a token run)."""
    q = [t for t in _q_tokens(question) if t]
    hits = []
    for term in terms:
        if not abbreviation_like(term):
            continue
        tt = [t for t in _q_tokens(term) if t]
        if tt and any(q[i:i + len(tt)] == tt for i in range(len(q) - len(tt) + 1)):
            hits.append(term)
    return hits


# ------------------------------------------------------------------ partner export

_PMC = re.compile(r"\bPMC\d{3,}\b")
_DOI = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+")


def locator(doc_id: str, metadata: Iterable[Mapping[str, Any]]) -> str | None:
    """The PMC id, else the DOI, found in the document's chunk metadata (string
    values, keys in sorted order) or its doc_id; ``None`` when neither resolves."""
    texts: list[str] = []
    for md in metadata:
        texts += [str(md[k]) for k in sorted(md) if isinstance(md[k], (str, int))]
    texts.append(doc_id)
    for pat in (_PMC, _DOI):
        for t in texts:
            m = pat.search(t)
            if m:
                return m.group(0).rstrip(".,;)")
    return None


def status_label(n: int, errors: int, wilson: Any) -> str:
    lo, hi = wilson(errors, n)
    return (f"scripted-clean, judged sample of {n}, error rate {100 * errors / n:.1f}% "
            f"(95% CI {100 * lo:.1f}–{100 * hi:.1f})")
