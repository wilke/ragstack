"""Compare a live Elasticsearch collection's metadata mapping against the
declared chunk-metadata schema (#603).

**Read-only, and report-only.** The only request it makes is
``GET <es>/<index>/_mapping`` (plus ``GET <es>/_cat/indices`` to enumerate) —
there is no code path in this module that issues anything but a GET. And it
exits ``0`` whether or not it finds divergence, because the fleet does not
conform and cannot be made to conform by a build failing: **an Elasticsearch
mapping cannot be changed in place.** Every divergence here is a reindex, and
reindexing ``open-access`` is 47.6M chunks / 82.9 GB. `Dengue` (382) and
`scratch_uiguide` (513) are minutes; the large corpora need a plan. A check that
failed CI would be turned off within a day and would have bought nothing; a
check that prints the list is what a migration is planned from. ``--fail-on``
exists for the day a tenant's collections *are* clean and someone wants to keep
them that way.

.. rubric:: What each verdict means, and what it does not

``ok``
    The index maps this declared field with the type the schema derives.
``divergent``
    The index maps it with a DIFFERENT type. This is the finding — it is what
    ``metadata.pmid`` being a ``long`` on one collection and a ``keyword`` on its
    peers looked like, and it means a cross-collection ``/v1/query`` filtering on
    that field behaves differently per member of the fused set.
``absent``
    Declared, but the index has no mapping for it. **Not a defect.** Elasticsearch
    materializes a field's mapping the first time a document carries it, so an
    absent field means "no document in this collection has ever had one" — which
    is the normal state of an optional field. It is listed only because a field
    that is absent today is a field whose type the NEXT writer still gets to
    decide, unless the index was created with the derived mapping.
``undeclared``
    The index maps a field the schema says nothing about. Also not a defect —
    the namespace is open by design. This is the backlog: each one is either a
    field to declare in ``contracts/schemas/chunk_metadata.json`` or a field to
    stop writing.

The report never says "wrong" or "broken", for the same reason
:mod:`ragstack.ops.store_inventory` never says "orphan": this tool sees one
mapping, not the corpus's intent, and a collection built for a purpose the
schema has not caught up with is not misbehaving.

.. rubric:: Why this is Elasticsearch-only

Qdrant does not have a mapping to diverge. It stores each payload value with the
type it was written with, per point, so two points in ONE collection can disagree
— there is nothing cluster-side to read and compare. That is worse, not better:
it is why ``{"year": 2021}`` matched 129,248 chunks on the ES leg and 0 on the
Qdrant leg of the same collection. The ingest-boundary check in
:func:`ragstack.metadata_schema.validate_chunks` is the defence on that side, and
it is a defence for new writes only. Auditing an existing Qdrant collection means
sampling points, which is a different tool with a different cost model.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from ragstack.metadata_schema import (
    DECLARED_FIELDS,
    ES_METADATA_CONTAINER,
    elasticsearch_metadata_properties,
)

log = logging.getLogger(__name__)

#: The dynamic template ``stores/elasticsearch.py`` installs so that an
#: UNDECLARED string field still gets a bounded keyword mapping instead of a bare
#: one. Its absence is a real finding on its own: an unbounded keyword takes a
#: >32 KB value as one Lucene term and aborts the whole bulk request.
TEMPLATE_NAME = "metadata_strings_as_keyword"

OK = "ok"
DIVERGENT = "divergent"
ABSENT = "absent"
UNDECLARED = "undeclared"

#: Indices Elasticsearch owns rather than this project.
_SYSTEM_PREFIX = "."


@dataclass(frozen=True)
class FieldVerdict:
    """One declared-or-observed metadata field on one index."""

    name: str
    verdict: str
    declared: str = ""  # the ES type the schema derives, "" when undeclared
    observed: str = ""  # the ES type the index actually maps, "" when absent


@dataclass
class IndexReport:
    """Every verdict for one index, plus what could not be read."""

    es_url: str
    index: str
    fields: list[FieldVerdict] = field(default_factory=list)
    has_template: bool = False
    error: str = ""

    def by_verdict(self, verdict: str) -> list[FieldVerdict]:
        return [f for f in self.fields if f.verdict == verdict]

    @property
    def conforms(self) -> bool:
        """No DIVERGENT field and the bounded-keyword template is installed.

        ``absent`` and ``undeclared`` deliberately do not count against it — see
        the module docstring.
        """
        return not self.error and self.has_template and not self.by_verdict(DIVERGENT)


def _mapping_type(mapping: Any) -> str:
    """The ES type of one mapping node.

    A node with sub-``properties`` and no ``type`` is an implicit ``object`` —
    ES's own default, and how a declared scalar would look if somebody wrote a
    nested document into it.
    """
    if not isinstance(mapping, dict):
        return ""
    declared = mapping.get("type")
    if isinstance(declared, str):
        return declared
    return "object" if "properties" in mapping else ""


def compare_mapping(mappings: dict[str, Any]) -> tuple[list[FieldVerdict], bool]:
    """Verdicts for one index's ``mappings`` body, and whether the template is on.

    Pure — no I/O — so the comparison can be unit-tested against a captured
    mapping without a live cluster, which is the only way this stays testable in
    a suite that must not touch a store.
    """
    props = mappings.get("properties") or {}
    container = props.get(ES_METADATA_CONTAINER) or {}
    observed = container.get("properties") or {}
    declared = elasticsearch_metadata_properties()

    verdicts: list[FieldVerdict] = []
    for name in sorted(declared):
        want = declared[name]["type"]
        if name not in observed:
            verdicts.append(FieldVerdict(name, ABSENT, declared=want))
            continue
        got = _mapping_type(observed[name])
        verdicts.append(
            FieldVerdict(name, OK if got == want else DIVERGENT, declared=want, observed=got)
        )
    for name in sorted(observed):
        if name not in DECLARED_FIELDS:
            verdicts.append(
                FieldVerdict(name, UNDECLARED, observed=_mapping_type(observed[name]))
            )

    templates = mappings.get("dynamic_templates") or []
    has_template = any(TEMPLATE_NAME in t for t in templates if isinstance(t, dict))
    return verdicts, has_template


def list_indices(
    es_url: str, *, api_key: str = "", client: httpx.Client | None = None
) -> list[str]:
    """Every non-system index on one cluster. ``GET /_cat/indices`` — read-only."""
    owns = client is None
    c = client or httpx.Client(timeout=30.0)
    try:
        r = c.get(
            f"{es_url.rstrip('/')}/_cat/indices",
            params={"format": "json", "h": "index"},
            headers={"Authorization": f"ApiKey {api_key}"} if api_key else {},
        )
        r.raise_for_status()
        names = [str(item.get("index", "")) for item in r.json()]
    finally:
        if owns:
            c.close()
    return sorted(n for n in names if n and not n.startswith(_SYSTEM_PREFIX))


def inspect_index(
    es_url: str, index: str, *, api_key: str = "", client: httpx.Client | None = None
) -> IndexReport:
    """Read one index's mapping and compare it. ``GET /<index>/_mapping`` — read-only.

    A failure is recorded on the report rather than raised: one unreachable
    cluster in a fleet sweep must not cost the operator the other twenty
    answers — the same rule :mod:`ragstack.ops.store_inventory` applies to its
    probes.
    """
    report = IndexReport(es_url=es_url, index=index)
    owns = client is None
    c = client or httpx.Client(timeout=30.0)
    try:
        r = c.get(
            f"{es_url.rstrip('/')}/{index}/_mapping",
            headers={"Authorization": f"ApiKey {api_key}"} if api_key else {},
        )
        r.raise_for_status()
        body = r.json()
        # The response is keyed by CONCRETE index name, which is not necessarily
        # the name asked for (an alias resolves to its target). Take the single
        # entry rather than assuming the key.
        entry = body.get(index) or (next(iter(body.values())) if body else {})
        mappings = (entry or {}).get("mappings") or {}
        report.fields, report.has_template = compare_mapping(mappings)
    except Exception as e:  # noqa: BLE001 — one bad index must not end the sweep
        report.error = f"{type(e).__name__}: {e}"
    finally:
        if owns:
            c.close()
    return report


def collect(
    targets: Iterable[tuple[str, str]], *, api_key: str = ""
) -> list[IndexReport]:
    """Inspect every ``(es_url, index)`` pair, sharing one HTTP client."""
    with httpx.Client(timeout=30.0) as c:
        return [inspect_index(u, i, api_key=api_key, client=c) for u, i in targets]


def resolve_targets(
    es_urls: Sequence[str],
    patterns: Sequence[str],
    *,
    api_key: str = "",
) -> list[tuple[str, str]]:
    """``(es_url, index)`` pairs to inspect.

    With no ``patterns``, every non-system index on each cluster. With patterns,
    the shell-glob matches — ``ragstack_lib_*`` being the useful one, since that
    is the prefix a collection's index carries.
    """
    targets: list[tuple[str, str]] = []
    with httpx.Client(timeout=30.0) as c:
        for url in es_urls:
            try:
                names = list_indices(url, api_key=api_key, client=c)
            except Exception as e:  # noqa: BLE001 — see inspect_index
                log.warning("could not list indices on %s: %s", url, e)
                continue
            for name in names:
                if not patterns or any(fnmatch.fnmatch(name, p) for p in patterns):
                    targets.append((url, name))
    return targets


def render_text(reports: list[IndexReport], *, verbose: bool = False) -> str:
    """The human report: one block per index, divergence first."""
    lines: list[str] = []
    lines.append(
        "chunk-metadata mapping conformance — contracts/schemas/chunk_metadata.json"
    )
    lines.append(
        "REPORT ONLY. An ES mapping cannot be changed in place; every divergence "
        "below is a reindex."
    )
    lines.append("")
    for r in sorted(reports, key=lambda r: (r.es_url, r.index)):
        if r.error:
            lines.append(f"{r.index}  [{r.es_url}]")
            lines.append(f"  unreadable: {r.error}")
            lines.append("")
            continue
        divergent = r.by_verdict(DIVERGENT)
        undeclared = r.by_verdict(UNDECLARED)
        absent = r.by_verdict(ABSENT)
        ok = r.by_verdict(OK)
        flag = "CONFORMS" if r.conforms else "DIVERGES"
        lines.append(f"{r.index}  [{r.es_url}]  {flag}")
        lines.append(
            f"  {len(ok)} declared field(s) match, {len(divergent)} diverge, "
            f"{len(absent)} not materialized, {len(undeclared)} undeclared"
        )
        if not r.has_template:
            lines.append(
                f"  missing dynamic template {TEMPLATE_NAME!r} — an undeclared string "
                "field here maps as an UNBOUNDED keyword, and a >32 KB value aborts "
                "the whole bulk request"
            )
        for f in divergent:
            lines.append(
                f"  DIVERGENT  metadata.{f.name}: mapped {f.observed!r}, "
                f"declared {f.declared!r}"
            )
        if undeclared:
            lines.append(
                "  undeclared: "
                + ", ".join(f"{f.name}({f.observed})" for f in undeclared)
            )
        if verbose and absent:
            lines.append("  not materialized: " + ", ".join(f.name for f in absent))
        lines.append("")

    diverging = [r for r in reports if not r.conforms and not r.error]
    unreadable = [r for r in reports if r.error]
    lines.append(
        f"{len(reports)} index(es) inspected; {len(reports) - len(diverging) - len(unreadable)} "
        f"conform, {len(diverging)} diverge, {len(unreadable)} unreadable"
    )
    return "\n".join(lines)


def to_dict(reports: list[IndexReport]) -> dict[str, Any]:
    """The machine-readable report — for diffing two runs as a migration lands."""
    return {
        "schema": "contracts/schemas/chunk_metadata.json",
        "declared_fields": {n: m["type"] for n, m in elasticsearch_metadata_properties().items()},
        "indices": [
            {
                "elasticsearch": r.es_url,
                "index": r.index,
                "error": r.error,
                "has_dynamic_template": r.has_template,
                "conforms": r.conforms,
                "fields": [
                    {
                        "name": f.name,
                        "verdict": f.verdict,
                        "declared": f.declared,
                        "observed": f.observed,
                    }
                    for f in r.fields
                ],
            }
            for r in sorted(reports, key=lambda r: (r.es_url, r.index))
        ],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="metadata_conformance",
        description=(
            "Report where a live Elasticsearch index's metadata mapping diverges "
            "from the declared chunk-metadata schema. Read-only; exits 0 on "
            "divergence unless --fail-on-divergence is given."
        ),
    )
    ap.add_argument(
        "--es-url",
        action="append",
        default=[],
        metavar="URL",
        help="Elasticsearch base URL; repeatable.",
    )
    ap.add_argument(
        "--index",
        action="append",
        default=[],
        metavar="GLOB",
        help="Only indices matching this shell glob; repeatable. Default: all.",
    )
    ap.add_argument("--api-key", default="", help="ES API key, if the cluster needs one.")
    ap.add_argument("--json", action="store_true", help="Machine-readable output.")
    ap.add_argument(
        "--fail-on-divergence",
        action="store_true",
        help=(
            "Exit 1 when any inspected index diverges. OFF by default and "
            "deliberately so: the deployed fleet does not conform, and a mapping "
            "cannot be fixed in place."
        ),
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    if not args.es_url:
        ap.error("at least one --es-url is required")

    targets = resolve_targets(args.es_url, args.index, api_key=args.api_key)
    if not targets:
        print("no indices matched", file=sys.stderr)
        return 0
    reports = collect(targets, api_key=args.api_key)

    if args.json:
        print(json.dumps(to_dict(reports), indent=2))
    else:
        print(render_text(reports, verbose=args.verbose))

    if args.fail_on_divergence and any(not r.conforms for r in reports):
        return 1
    return 0
