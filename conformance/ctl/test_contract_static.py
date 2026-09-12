"""Static conformance of ``contracts/ctl`` — no server needed.

Runs ``contracts/ctl/validate.py``'s checks as pytest assertions (every
``$ref`` resolves, every operation has ``x-ctl-role`` and a matrix row, every
plan path is present, every schema is valid draft 2020-12 and closed, the verb
enum is the plan's, the settings-name guard is identical in both schemas and
actually rejects the forbidden names) and adds the one check the validator
does not own: that no schema PROPERTY NAME looks like a secret outside the
documented allowlist — the contract-level version of the runtime tree walk in
``test_fleet.py`` / ``test_tenants.py``.

Reading the contract is not importing the implementation.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Iterator

import pytest

from ctl.helpers import SCHEMA_ALLOWLIST, SECRET_NAME_RE

CONTRACT_DIR = Path(__file__).resolve().parents[2] / "contracts" / "ctl"


def _load_validator():
    spec = importlib.util.spec_from_file_location("ctl_contract_validate", CONTRACT_DIR / "validate.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def validator():
    return _load_validator()


def test_validator_passes(validator) -> None:
    findings = validator.check()
    assert findings == [], "\n".join(findings)


def test_every_plan_path_is_present(validator) -> None:
    spec = validator.load_openapi()
    missing = [p for p in validator.PLAN_PATHS if p not in spec["paths"]]
    assert missing == []


def test_every_operation_has_a_role_and_a_matrix_row(validator) -> None:
    spec = validator.load_openapi()
    rows = {r["operationId"]: r for r in spec["x-ctl-authorization-matrix"]}
    for path, method, op in validator.iter_operations(spec):
        assert op.get("x-ctl-role") in validator.ROLES, f"{method.upper()} {path}"
        assert op["operationId"] in rows, f"{op['operationId']} has no matrix row"
        assert rows[op["operationId"]]["role"] == op["x-ctl-role"]


def test_matrix_roles_are_the_planned_split(validator) -> None:
    """The plan's table: which operations are operator-only."""
    spec = validator.load_openapi()
    by_id = {r["operationId"]: r["role"] for r in spec["x-ctl-authorization-matrix"]}
    assert by_id["ctlHealth"] == "anonymous"
    for viewer_op in ("ctlVersion", "ctlMe", "ctlFleet", "ctlTenantsList", "ctlTenantShow",
                      "ctlTenantEnv", "ctlDoctor", "ctlGatewayStatus", "ctlGatewayRender",
                      "ctlJobsList", "ctlJobShow", "ctlSettingsGet"):
        assert by_id[viewer_op] == "viewer", viewer_op
    for operator_op in ("ctlTenantCreate", "ctlTenantLogs", "ctlTenantOp", "ctlGatewayApply",
                        "ctlAudit", "ctlJobStepLog", "ctlJobSecrets", "ctlJobResume",
                        "ctlJobContinue", "ctlJobCancel", "ctlSettingsPut"):
        assert by_id[operator_op] == "operator", operator_op


def test_secrets_endpoint_refuses_sessions(validator) -> None:
    spec = validator.load_openapi()
    op = spec["paths"]["/v1/jobs/{id}/secrets"]["get"]
    schemes = {name for alt in op["security"] for name in alt}
    assert "SessionAuth" not in schemes
    assert "no-store" in str(op["responses"]["200"]["headers"]["Cache-Control"])
    assert "410" in op["responses"]


def test_error_codes_are_the_planned_set(validator) -> None:
    schemas = validator.load_schemas()
    codes = set(schemas["error.json"]["properties"]["code"]["enum"])
    assert codes == {
        "auth_required", "forbidden", "both_credentials", "not_found", "validation",
        "locked", "plan_stale", "duplicate", "doctor_red", "confirm_required",
        "refused", "rate_limited", "internal",
    }


def _property_names(node: Any, where: str = "$") -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            for name in props:
                yield f"{where}.properties.{name}", name
        for k, v in node.items():
            if k in ("enum", "const", "default", "description"):
                continue
            yield from _property_names(v, f"{where}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _property_names(v, f"{where}[{i}]")


def test_no_schema_property_name_looks_like_a_secret(validator) -> None:
    """A contract that names a field ``api_key`` would pass every runtime check
    that reads an empty fleet. Pin it at the schema level: every property name
    in every schema either does not match the secret-name regex or is on the
    documented allowlist (``helpers.SCHEMA_ALLOWLIST``, each with its reason)."""
    schemas = validator.load_schemas()
    offenders = [
        f"{fname} {where}"
        for fname, schema in schemas.items()
        for where, name in _property_names(schema)
        if SECRET_NAME_RE.search(name) and name not in SCHEMA_ALLOWLIST
    ]
    assert offenders == [], offenders


def _guard(validator, fname: str) -> dict:
    d = validator.load_schemas()[fname]["$defs"]["PublicSettingKey"]
    return {k: v for k, v in d.items() if k != "description"}


def test_settings_guard_is_one_pattern(validator) -> None:
    """The two copies are the SAME guard, compared whole.

    Naming the members (``pattern``, ``not``) is how this went vacuous once
    before: the exceptions moved into an ``anyOf`` and ``a["not"] == b["not"]``
    quietly became ``None == None``. Compare everything but the description.
    """
    a, b = _guard(validator, "registry.json"), _guard(validator, "create_request.json")
    assert a == b
    assert a["pattern"] and a["anyOf"], a


def test_settings_guard_mirrors_the_go_classifier(validator) -> None:
    """The contract must forbid at least what ``settings.go`` calls secret.

    The pre-#531 pattern was anchored
    (``^(API_KEYS|…|NEO4J_AUTH)$|(_DSN|PASSWORD|TOKEN|_API_KEY)$``), so a
    secret-shaped substring in the MIDDLE of a name passed:
    ``TENANT_API_KEY_USER``, ``AWS_SECRET_ACCESS_KEY`` and ``SSH_PRIVATE_KEY``
    were all storable in the registry as public settings, while the daemon's own
    classifier called them secrets and its redactors stripped their values.
    """
    import jsonschema
    from referencing import Registry, Resource

    guard = _guard(validator, "registry.json")
    forbidden = next(b["not"]["pattern"] for b in guard["anyOf"] if "not" in b)
    allow = next(b["enum"] for b in guard["anyOf"] if "enum" in b)
    assert forbidden == validator.GO_SECRET_PATTERN, "not settings.go's secretPattern"
    assert allow == validator.GO_PUBLIC_DESPITE_PATTERN, "not settings.go's publicDespitePattern"

    schemas = validator.load_schemas()
    registry = Registry().with_resource(
        "registry.json", Resource.from_contents(schemas["registry.json"])
    )
    v = jsonschema.Draft202012Validator(
        {"$ref": "registry.json#/$defs/PublicSettings"}, registry=registry
    )
    accepted = [n for n in validator.FORBIDDEN_SETTING_NAMES if v.is_valid({n: "x"})]
    assert accepted == [], f"PublicSettings accepted secret-class names: {accepted}"
    rejected = [n for n in validator.PUBLIC_SETTING_NAMES if not v.is_valid({n: "x"})]
    assert rejected == [], f"PublicSettings rejected public names: {rejected}"
