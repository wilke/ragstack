"""Assertion helpers for the control-plane suite. Contract-reading only."""

from __future__ import annotations

import re
from typing import Any, Callable, Iterator

import httpx
import jsonschema
from referencing import Registry, Resource

#: The header/body correlation id format (``components/headers/XRequestId``).
RID_RE = re.compile(r"^[0-9a-f]{16}$")

#: A field NAME that would suggest a secret is being carried. Applied to every
#: key in a read response's JSON tree; see :func:`find_secret_names`.
SECRET_NAME_RE = re.compile(r"(?i)(api_key|password|secret|token|dsn)")

#: Field names that MATCH the regex but are, by contract, not secrets — each
#: with why. The runtime checks use the subset that can appear in a read
#: response; the static check over the schemas adds the request-side names.
#:
#: ``secret_refs``          registry: ``[{key, file}]`` — the NAME of a secret
#:                          and which file holds it (``registry.json``).
#: ``secrets_file_sha256``  registry: a digest of ``secrets.env`` for drift
#:                          detection; not invertible.
READ_ALLOWLIST = frozenset({"secret_refs", "secrets_file_sha256"})
#: ``secrets``              ``secrets_response.json`` (the deliver-once envelope,
#:                          never read by this suite) and ``bundle_manifest``'s
#:                          description of the encrypted payload.
#: ``ctl_api_key``          the request-body member a browser re-presents.
SCHEMA_ALLOWLIST = READ_ALLOWLIST | frozenset({"secrets", "ctl_api_key"})

#: String VALUES that look like credentials: a ``token_hex(32)`` (what every
#: tenant key and ctl key is), a BV-BRC signature tail, a PEM private key.
SECRET_VALUE_RES = (
    re.compile(r"^[0-9a-f]{64}$"),
    re.compile(r"\|sig=[0-9a-f]{64,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)

#: Digest-shaped fields whose 64-hex VALUE is not a secret. Anything else that
#: is 64 lowercase hex in a read response is treated as a leaked key.
DIGEST_FIELDS = frozenset({
    "sha256", "env_file_sha256", "secrets_file_sha256", "lockhash", "sha256sums",
    "session_id",
})


def _walk(node: Any, path: str = "$") -> Iterator[tuple[str, str, Any]]:
    """Yield ``(json_path, key, value)`` for every object member in *node*."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield f"{path}.{k}", k, v
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


def find_secret_names(tree: Any, allow: frozenset[str] = READ_ALLOWLIST) -> list[str]:
    """JSON paths of every object key matching :data:`SECRET_NAME_RE`, minus
    *allow*. Recursive over dicts and lists. Empty list = clean."""
    return [
        path for path, key, _ in _walk(tree)
        if SECRET_NAME_RE.search(key) and key not in allow
    ]


def find_secret_values(tree: Any) -> list[str]:
    """JSON paths of every string value that looks like a credential."""
    hits: list[str] = []
    for path, key, value in _walk(tree):
        if not isinstance(value, str):
            continue
        for rx in SECRET_VALUE_RES:
            if rx.search(value) and not (rx is SECRET_VALUE_RES[0] and key in DIGEST_FIELDS):
                hits.append(path)
                break
    return hits


def forbidden_setting_name(schemas: dict[str, dict]) -> Callable[[str], bool]:
    """"Is this env key secret-class *by the registry's own guard*?"

    Read out of the contract rather than copied, so the runtime check enforces
    exactly what the schema enforces. The guard is

    ``anyOf: [{enum: <allowlist>}, {not: {pattern: <secretPattern>}}]``

    — a name is forbidden when it matches the pattern AND is not on the
    allowlist. Reading the ``not`` branch alone would flag the six reviewed
    ``publicDespitePattern`` exceptions (``CHUNK_MAX_TOKENS`` and friends) that
    the schema itself accepts, making the runtime check STRICTER than the
    contract it claims to be reading.
    """
    guard = schemas["registry"]["$defs"]["PublicSettingKey"]
    branches = guard.get("anyOf") or [guard]
    pattern = next(b["not"]["pattern"] for b in branches if isinstance(b.get("not"), dict))
    allow = frozenset(next((b["enum"] for b in branches if "enum" in b), ()))
    rx = re.compile(pattern)
    return lambda name: bool(rx.search(name)) and name not in allow


def _registry(schemas: dict[str, dict]) -> Registry:
    reg: Registry = Registry()
    for schema in schemas.values():
        reg = reg.with_resource(schema["$id"], Resource.from_contents(schema))
    return reg


def validate(instance: Any, schema_name: str, schemas: dict[str, dict]) -> None:
    """Validate *instance* against ``contracts/ctl/schemas/<schema_name>.json``,
    resolving cross-file ``$ref`` (``registry.json#/$defs/Tenant`` etc.)."""
    schema = schemas[schema_name]
    validator = jsonschema.Draft202012Validator(schema, registry=_registry(schemas))
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    assert not errors, "\n".join(
        f"{schema_name}: {'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in errors[:10]
    )


def assert_request_id(resp: httpx.Response) -> str:
    rid = resp.headers.get("X-Request-Id")
    assert rid, f"no X-Request-Id on a {resp.status_code} from {resp.request.method} {resp.request.url.path}"
    assert RID_RE.match(rid), f"X-Request-Id {rid!r} does not match {RID_RE.pattern}"
    return rid


def assert_error(
    resp: httpx.Response, status: int, code: str, schemas: dict[str, dict]
) -> dict:
    """The response is *status*, its body is an ``error.json`` with *code*, and
    its ``request_id`` equals the ``X-Request-Id`` header."""
    assert resp.status_code == status, (
        f"expected {status} {code} from {resp.request.method} {resp.request.url.path}, "
        f"got {resp.status_code}: {resp.text[:300]}"
    )
    body = resp.json()
    validate(body, "error", schemas)
    assert body["code"] == code, f"expected code {code!r}, got {body['code']!r}: {body['detail']}"
    assert body["request_id"] == assert_request_id(resp)
    return body
