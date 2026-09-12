#!/usr/bin/env python3
"""Static checks for the control-plane contract (``contracts/ctl``).

argv-only; no server, no imports from ``python/`` or ``go/``. Run it directly
(``python contracts/ctl/validate.py``) or through
``conformance/ctl/test_contract_static.py``, which calls :func:`check` and
reports each finding as its own assertion.

What it proves:

* ``openapi.yaml`` parses, is OpenAPI 3.1, and every ``$ref`` in it resolves —
  ``#/components/...`` pointers into the document itself, and
  ``./schemas/<file>.json[#/pointer]`` references to schema files on disk.
* Every schema file under ``schemas/`` is a valid draft 2020-12 schema
  (``Draft202012Validator.check_schema``), its ``$id`` equals its file name,
  and every ``$ref`` inside it — local ``#/$defs/...`` or cross-file
  ``other.json#/$defs/...`` — resolves.
* Every operation carries ``x-ctl-role`` with a known value, is listed in the
  top-level ``x-ctl-authorization-matrix`` with the same role, method and
  path, and vice versa.
* Every path the plan's "ctl HTTP API" table names is present, with the
  methods it names.
* The ``verb`` path parameter's enum is exactly the plan's verb list, and
  every verb has an ``x-ctl-op-args`` schema that is itself valid.
* The forbidden-settings-name guard is byte-identical between
  ``registry.json`` and ``create_request.json``, its pattern is
  ``settings.go``'s ``secretPattern`` and its allowlist that file's
  ``publicDespitePattern``, and it actually refuses the forbidden names and
  accepts the public ones (:data:`FORBIDDEN_SETTING_NAMES` /
  :data:`PUBLIC_SETTING_NAMES`).
* Every object schema is closed: ``additionalProperties: false``, or a map
  with ``propertyNames`` and a typed ``additionalProperties``.

Exit status 0 when every check passes; 1 with one line per finding otherwise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterator

import jsonschema
import yaml

HERE = Path(__file__).resolve().parent
OPENAPI = HERE / "openapi.yaml"
SCHEMAS_DIR = HERE / "schemas"

ROLES = {"anonymous", "viewer", "operator"}

#: The plan's "ctl HTTP API" table, method by path. A path missing here is a
#: contract regression; a path present here but not in the plan needs the plan
#: amended first.
PLAN_PATHS: dict[str, set[str]] = {
    "/health": {"get"},
    "/v1/version": {"get"},
    "/v1/me": {"get"},
    "/v1/session": {"post", "delete"},
    "/v1/fleet": {"get"},
    "/v1/tenants": {"get", "post"},
    "/v1/tenants/{name}": {"get"},
    "/v1/tenants/{name}/env": {"get"},
    "/v1/tenants/{name}/logs": {"get"},
    "/v1/tenants/{name}/ops/{verb}": {"post"},
    "/v1/doctor": {"get"},
    "/v1/gateway": {"get"},
    "/v1/gateway/render": {"post"},
    "/v1/gateway/apply": {"post"},
    "/v1/audit": {"get"},
    "/v1/jobs": {"get"},
    "/v1/jobs/{id}": {"get"},
    "/v1/jobs/{id}/steps/{n}/log": {"get"},
    "/v1/jobs/{id}/secrets": {"get"},
    "/v1/jobs/{id}/resume": {"post"},
    "/v1/jobs/{id}/continue": {"post"},
    "/v1/jobs/{id}/cancel": {"post"},
    "/v1/settings": {"get", "put"},
}

#: The plan's verb list, in the plan's order.
PLAN_VERBS = [
    "start", "stop", "restart", "backup", "restore", "handover", "migrate-local",
    "decommission", "key-mint", "key-revoke", "admin-add", "admin-remove",
    "sa-create", "sa-disable", "sa-enable", "env-set", "env-unset", "env-normalize",
    "render-units", "update-code",
]

METHODS = {"get", "put", "post", "delete", "patch", "options", "head", "trace"}


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load_openapi() -> dict[str, Any]:
    with open(OPENAPI, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_schemas() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(SCHEMAS_DIR.glob("*.json")):
        with open(path, encoding="utf-8") as fh:
            out[path.name] = json.load(fh)
    return out


# --------------------------------------------------------------------------- #
# $ref walking
# --------------------------------------------------------------------------- #
def walk_refs(node: Any, where: str = "$") -> Iterator[tuple[str, str]]:
    """Yield ``(json_path, ref)`` for every ``$ref`` string under *node*."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str):
                yield where, v
            else:
                yield from walk_refs(v, f"{where}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_refs(v, f"{where}[{i}]")


def resolve_pointer(doc: Any, pointer: str) -> bool:
    """``True`` when the RFC 6901 *pointer* (``/a/b``) exists in *doc*."""
    if pointer in ("", "/"):
        return True
    cur = doc
    for raw in pointer.lstrip("/").split("/"):
        seg = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        elif isinstance(cur, list) and seg.isdigit() and int(seg) < len(cur):
            cur = cur[int(seg)]
        else:
            return False
    return True


def split_ref(ref: str) -> tuple[str, str]:
    """``'x.json#/a'`` → ``('x.json', '/a')``; ``'#/a'`` → ``('', '/a')``."""
    if "#" in ref:
        base, frag = ref.split("#", 1)
        return base, frag
    return ref, ""


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def check_openapi_refs(spec: dict, schemas: dict[str, dict]) -> Iterator[str]:
    for where, ref in walk_refs(spec):
        base, frag = split_ref(ref)
        if base == "":
            if not resolve_pointer(spec, frag):
                yield f"openapi.yaml {where}: $ref {ref!r} does not resolve in the document"
            continue
        if base.startswith("./schemas/"):
            fname = base[len("./schemas/"):]
        elif base.startswith("schemas/"):
            fname = base[len("schemas/"):]
        else:
            yield f"openapi.yaml {where}: $ref {ref!r} is neither a local pointer nor ./schemas/<file>"
            continue
        if fname not in schemas:
            yield f"openapi.yaml {where}: $ref {ref!r} names a schema file that does not exist"
        elif not resolve_pointer(schemas[fname], frag):
            yield f"openapi.yaml {where}: $ref {ref!r} pointer does not resolve in {fname}"


def check_schema_files(schemas: dict[str, dict]) -> Iterator[str]:
    for fname, schema in schemas.items():
        try:
            jsonschema.Draft202012Validator.check_schema(schema)
        except jsonschema.SchemaError as e:  # pragma: no cover - reported, not raised
            yield f"{fname}: not a valid draft 2020-12 schema: {e.message}"
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            yield f"{fname}: $schema is not draft 2020-12"
        if schema.get("$id") != fname:
            yield f"{fname}: $id {schema.get('$id')!r} != file name"
        if not schema.get("title"):
            yield f"{fname}: no title"
        for where, ref in walk_refs(schema):
            base, frag = split_ref(ref)
            target = schema if base == "" else schemas.get(base)
            if target is None:
                yield f"{fname} {where}: $ref {ref!r} names a schema file that does not exist"
            elif not resolve_pointer(target, frag):
                yield f"{fname} {where}: $ref {ref!r} pointer does not resolve"


def _is_object_schema(node: dict) -> bool:
    t = node.get("type")
    return t == "object" or (isinstance(t, list) and "object" in t) or "properties" in node


def check_closed_objects(schemas: dict[str, dict]) -> Iterator[str]:
    """Every object is ``additionalProperties: false`` or a typed, name-guarded map."""

    def walk(node: Any, fname: str, where: str) -> Iterator[str]:
        if isinstance(node, dict):
            if _is_object_schema(node) and "$ref" not in node:
                ap = node.get("additionalProperties", True)
                if ap is False:
                    pass
                elif isinstance(ap, dict) and "propertyNames" in node:
                    pass
                elif where.endswith(".extra") or where.endswith(".args") or where.endswith(".result") or where.endswith(".args_redacted"):
                    # The documented free-form members: Error.extra, OpRequest.args,
                    # Job.result, AuditRow.args_redacted.
                    pass
                else:
                    yield f"{fname} {where}: object schema is not closed (additionalProperties: false, or propertyNames + typed additionalProperties)"
            for k, v in node.items():
                if k in ("enum", "const", "default", "examples"):
                    continue
                yield from walk(v, fname, f"{where}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from walk(v, fname, f"{where}[{i}]")

    for fname, schema in schemas.items():
        yield from walk(schema, fname, "$")


def iter_operations(spec: dict) -> Iterator[tuple[str, str, dict]]:
    for path, item in (spec.get("paths") or {}).items():
        for method, op in (item or {}).items():
            if method in METHODS:
                yield path, method, op


def check_operations(spec: dict) -> Iterator[str]:
    matrix = spec.get("x-ctl-authorization-matrix")
    if not isinstance(matrix, list):
        yield "openapi.yaml: top-level x-ctl-authorization-matrix is missing or not a list"
        matrix = []
    by_opid = {row.get("operationId"): row for row in matrix}
    seen: set[str] = set()

    for path, method, op in iter_operations(spec):
        opid = op.get("operationId")
        if not opid:
            yield f"{method.upper()} {path}: no operationId"
            continue
        if opid in seen:
            yield f"{method.upper()} {path}: duplicate operationId {opid}"
        seen.add(opid)
        role = op.get("x-ctl-role")
        if role not in ROLES:
            yield f"{opid}: x-ctl-role {role!r} is not one of {sorted(ROLES)}"
        row = by_opid.get(opid)
        if row is None:
            yield f"{opid}: not in x-ctl-authorization-matrix"
        else:
            if row.get("role") != role:
                yield f"{opid}: matrix role {row.get('role')!r} != x-ctl-role {role!r}"
            if row.get("method", "").lower() != method:
                yield f"{opid}: matrix method {row.get('method')!r} != {method.upper()}"
            if row.get("path") != path:
                yield f"{opid}: matrix path {row.get('path')!r} != {path!r}"
            if not isinstance(row.get("session"), bool):
                yield f"{opid}: matrix row has no boolean `session`"
            if "viewer_fields" not in row:
                yield f"{opid}: matrix row has no `viewer_fields`"
        # security agrees with role
        sec = op.get("security")
        if role == "anonymous":
            if sec != []:
                yield f"{opid}: anonymous operation must declare `security: []`"
        else:
            if not sec:
                yield f"{opid}: authenticated operation declares no security"
            elif row is not None:
                schemes = {name for alt in sec for name in alt}
                if row.get("session") and "SessionAuth" not in schemes:
                    yield f"{opid}: matrix says session: true but SessionAuth is not a security option"
                if not row.get("session") and "SessionAuth" in schemes:
                    yield f"{opid}: matrix says session: false but SessionAuth is a security option"
        # mutations: request body is the envelope, responses 200 Plan + 202 Job
        if method in ("post", "put") and role == "operator":
            body = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema") or {}
            if body.get("$ref") not in ("#/components/schemas/OpRequest", "#/components/schemas/CreateRequest"):
                yield f"{opid}: operator mutation must take OpRequest or CreateRequest, got {body}"
            resp = op.get("responses") or {}
            for code in ("200", "202", "409", "422", "428"):
                if code not in resp:
                    yield f"{opid}: mutation lacks a {code} response"
        # every response has X-Request-Id, and every error response is Error
        for code, resp in (op.get("responses") or {}).items():
            if "$ref" in resp:
                target = resp["$ref"].split("/")[-1]
                resp = ((spec.get("components") or {}).get("responses") or {}).get(target) or {}
            if "X-Request-Id" not in (resp.get("headers") or {}):
                yield f"{opid}: response {code} has no X-Request-Id header"
            if code[0] in "45":
                schema = (((resp.get("content") or {}).get("application/json") or {}).get("schema") or {})
                if schema.get("$ref") != "#/components/schemas/Error":
                    yield f"{opid}: error response {code} does not reference the Error schema"

    for opid, row in by_opid.items():
        if opid not in seen:
            yield f"matrix row {opid!r} names an operation that does not exist"


def check_plan_paths(spec: dict) -> Iterator[str]:
    paths = spec.get("paths") or {}
    for path, methods in PLAN_PATHS.items():
        if path not in paths:
            yield f"plan path {path} is missing"
            continue
        present = {m for m in (paths[path] or {}) if m in METHODS}
        for m in methods - present:
            yield f"plan path {path}: method {m.upper()} is missing"
    for path in paths:
        if path not in PLAN_PATHS:
            yield f"path {path} is not in the plan's table (amend PLAN_PATHS and the plan first)"


def check_verbs(spec: dict) -> Iterator[str]:
    verb_param = ((spec.get("components") or {}).get("parameters") or {}).get("Verb") or {}
    enum = (verb_param.get("schema") or {}).get("enum")
    if enum != PLAN_VERBS:
        yield f"Verb enum differs from the plan's verb list: {enum} != {PLAN_VERBS}"
    op_args = spec.get("x-ctl-op-args")
    if not isinstance(op_args, dict):
        yield "x-ctl-op-args is missing"
        return
    for verb in PLAN_VERBS:
        if verb not in op_args:
            yield f"x-ctl-op-args has no entry for verb {verb!r}"
            continue
        try:
            jsonschema.Draft202012Validator.check_schema(op_args[verb])
        except jsonschema.SchemaError as e:  # pragma: no cover
            yield f"x-ctl-op-args[{verb!r}] is not a valid schema: {e.message}"
        if op_args[verb].get("additionalProperties", True) is not False:
            yield f"x-ctl-op-args[{verb!r}] must be additionalProperties: false"
    for verb in op_args:
        if verb not in PLAN_VERBS:
            yield f"x-ctl-op-args names {verb!r}, which is not a verb"
    # The doctor `op` enum is the verbs plus the non-verb ops.
    doctor = ((spec.get("paths") or {}).get("/v1/doctor") or {}).get("get") or {}
    for p in doctor.get("parameters") or []:
        if p.get("name") == "op":
            got = (p.get("schema") or {}).get("enum") or []
            if got[: len(PLAN_VERBS)] != PLAN_VERBS:
                yield "doctor `op` enum must start with the verb list in order"


#: ``go/internal/ctl/settings/settings.go``'s ``secretPattern``, byte for byte.
#: The daemon's classifier is what decides where a key may live and what the
#: redactors strip; a contract that forbids LESS than the classifier lets the
#: registry store exactly the names the daemon calls secret. The pre-#531
#: spelling (``^(API_KEYS|API_KEY_TENANTS|API_KEY_ROLES|NEO4J_AUTH)$|(_DSN|
#: PASSWORD|TOKEN|_API_KEY)$``) did that: its anchors meant the secret-shaped
#: part had to sit at one END of the name, so ``TENANT_API_KEY_USER``,
#: ``AWS_SECRET_ACCESS_KEY`` and ``SSH_PRIVATE_KEY`` all passed.
GO_SECRET_PATTERN = r"(API_KEY[A-Z_]*|_KEY$|SECRET|PASSWORD|TOKEN|DSN|AUTH)"

#: ``settings.go``'s ``publicDespitePattern``: the six ragstack settings whose
#: NAME trips the pattern but whose value is a number or an identifier, never a
#: credential. Mirrored here so drift in either direction is a finding.
GO_PUBLIC_DESPITE_PATTERN = [
    "CHUNK_MAX_TOKENS",
    "CHUNK_TOKEN_COUNTER",
    "EMBEDDING_MAX_BATCH_TOKENS",
    "EMBEDDING_CHARS_PER_TOKEN",
    "GOWE_RECEIPTS_OUTPUT_KEY",
    "GOWE_SHARDS_INPUT_KEY",
]

#: Names the guard must REFUSE. The last three are the ones the anchored
#: pattern let through.
FORBIDDEN_SETTING_NAMES = [
    "API_KEYS", "API_KEY_TENANTS", "API_KEY_ROLES", "POSTGRES_DSN",
    "NEO4J_PASSWORD", "GOWE_TOKEN", "OPENAI_API_KEY", "NEO4J_AUTH",
    "TENANT_API_KEY_USER", "AWS_SECRET_ACCESS_KEY", "SSH_PRIVATE_KEY",
]

#: Names the guard must ACCEPT: ordinary public settings, plus the six reviewed
#: exceptions that the pattern alone would refuse.
PUBLIC_SETTING_NAMES = [
    "LOG_LEVEL", "MAX_COLLECTIONS_PER_OWNER", "GRAPH_BACKEND", "ES_JAVA_OPTS",
    *GO_PUBLIC_DESPITE_PATTERN,
]


def check_settings_guard(schemas: dict[str, dict]) -> Iterator[str]:
    reg = (schemas.get("registry.json") or {}).get("$defs", {}).get("PublicSettingKey")
    cre = (schemas.get("create_request.json") or {}).get("$defs", {}).get("PublicSettingKey")
    if reg is None or cre is None:
        yield "PublicSettingKey is missing from registry.json or create_request.json"
        return
    # Everything but the human-facing `description` must be identical — not the
    # two members this used to name by hand. The exceptions now live in an
    # `anyOf`, and a comparison that enumerates members goes vacuous the next
    # time the shape changes (`reg.get("not")` was already `None == None` here).
    reg_body = {k: v for k, v in reg.items() if k != "description"}
    cre_body = {k: v for k, v in cre.items() if k != "description"}
    if reg_body != cre_body:
        yield (
            "PublicSettingKey differs between registry.json and create_request.json: "
            f"{json.dumps(reg_body, sort_keys=True)} != {json.dumps(cre_body, sort_keys=True)}"
        )

    # The guard is `pattern` (shape) AND `anyOf: [allowlist, not(secretPattern)]`.
    branches = reg.get("anyOf") or []
    allow = next((b.get("enum") for b in branches if isinstance(b, dict) and "enum" in b), None)
    forbidden = next(
        (b["not"].get("pattern") for b in branches if isinstance(b, dict) and isinstance(b.get("not"), dict)),
        None,
    )
    if forbidden is None:
        yield "PublicSettingKey has no anyOf branch forbidding the secret-name pattern"
    elif forbidden != GO_SECRET_PATTERN:
        yield (
            "PublicSettingKey's forbidden pattern is not settings.go's secretPattern "
            f"byte for byte: {forbidden!r} != {GO_SECRET_PATTERN!r}"
        )
    if allow != GO_PUBLIC_DESPITE_PATTERN:
        yield (
            "PublicSettingKey's allowlist is not settings.go's publicDespitePattern: "
            f"{allow!r} != {GO_PUBLIC_DESPITE_PATTERN!r}"
        )
    if "(?" in (forbidden or "") or "(?" in reg.get("pattern", ""):
        yield "PublicSettingKey patterns must be RE2-compatible (no lookaround)"

    # Prove the guard behaves, not just that it is spelled.
    from referencing import Registry, Resource

    registry = Registry().with_resource("registry.json", Resource.from_contents(schemas["registry.json"]))
    v = jsonschema.Draft202012Validator({"$ref": "registry.json#/$defs/PublicSettings"}, registry=registry)
    for bad in FORBIDDEN_SETTING_NAMES:
        if v.is_valid({bad: "x"}):
            yield f"PublicSettings accepted forbidden key {bad}"
    for good in PUBLIC_SETTING_NAMES:
        if not v.is_valid({good: "x"}):
            yield f"PublicSettings rejected public key {good}"


def check(spec: dict | None = None, schemas: dict[str, dict] | None = None) -> list[str]:
    """Run every check; return the findings (empty = pass)."""
    spec = load_openapi() if spec is None else spec
    schemas = load_schemas() if schemas is None else schemas
    findings: list[str] = []
    if spec.get("openapi", "").split(".")[0:2] != ["3", "1"]:
        findings.append(f"openapi version {spec.get('openapi')!r} is not 3.1.x")
    if (spec.get("info") or {}).get("title") != "RAGStack Control Plane API":
        findings.append("info.title is not 'RAGStack Control Plane API'")
    findings += list(check_openapi_refs(spec, schemas))
    findings += list(check_schema_files(schemas))
    findings += list(check_closed_objects(schemas))
    findings += list(check_operations(spec))
    findings += list(check_plan_paths(spec))
    findings += list(check_verbs(spec))
    findings += list(check_settings_guard(schemas))
    return findings


def main(argv: list[str]) -> int:
    if argv[1:] not in ([], ["-q"]):
        print(f"usage: {argv[0]} [-q]", file=sys.stderr)
        return 2
    spec, schemas = load_openapi(), load_schemas()
    findings = check(spec, schemas)
    n_ops = sum(1 for _ in iter_operations(spec))
    if findings:
        for f in findings:
            print(f"FAIL {f}")
        print(f"{len(findings)} finding(s)")
        return 1
    if argv[1:] != ["-q"]:
        print(
            f"ok: {n_ops} operations over {len(spec['paths'])} paths, "
            f"{len(schemas)} schemas, every $ref resolves, every operation has x-ctl-role "
            f"and a matrix row, verb enum == plan ({len(PLAN_VERBS)})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
