"""Describe a tenant's API keys without ever emitting a key value.

Why this exists, and why it is built the way it is: on 2026-09-17 an agent
inspecting key counts leaked live key values into a transcript TWICE. First by
hand-rolling a ``KEY=value`` parser instead of using :func:`parse_env_file`.
Then — while writing this module to prevent that — by giving it a "helpful"
fallback: when JSON parsing failed it split the raw value on commas and rendered
the fragments, which are secrets, into a field it printed.

The second failure is the instructive one. A tool whose safety depends on
correctly recognising a format will leak the moment it does not. So the rule here
is structural, not careful:

    THE ONLY VALUE-DERIVED FIELD THAT LEAVES THIS MODULE IS A SHA-256 PREFIX.

No key head, no "recognisable prefix", no length-capped label, and no fallback
that turns unparsed text into a printable field. An unrecognised format yields a
record that says so and carries nothing.

The real shapes, measured across the live tenants:

===================  =============================================
``API_KEYS``         JSON **list** of key values, or a comma list
``API_KEY_TENANTS``  JSON **dict** ``{key: subject}``, or comma list
``API_KEY_ROLES``    JSON **dict** ``{key: role}``
===================  =============================================

Note ``API_KEYS`` is a *list* and the other two are *dicts keyed by the secret* —
the asymmetry that defeated the first two attempts.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ragstack.ops.store_inventory import parse_env_file

TENANT_ROOT = "/rag/data/tenants"
REDACTED = "<redacted>"
UNPARSED = "(unparsed)"


class Secret:
    """A string that will not print itself.

    ``repr``, ``str`` and ``format`` render ``<redacted>``, so a Secret is safe
    in an f-string, a log line, a traceback, and ``json.dumps(..., default=str)``.
    Equality and hashing use the real value so it remains usable as a dict key.
    """

    __slots__ = ("_v",)

    def __init__(self, value: str) -> None:
        self._v = value

    def reveal(self) -> str:
        """The actual value. The only way out, and deliberately hard to type."""
        return self._v

    def fingerprint(self, n: int = 12) -> str:
        return hashlib.sha256(self._v.encode()).hexdigest()[:n]

    def __repr__(self) -> str: return REDACTED
    def __str__(self) -> str: return REDACTED
    def __format__(self, spec: str) -> str: return REDACTED
    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and self._v == other._v
    def __hash__(self) -> int: return hash(self._v)
    def __len__(self) -> int: return len(self._v)


@dataclass(frozen=True)
class ApiKeyInfo:
    """One configured key. ``fingerprint`` is the only value-derived field."""

    fingerprint: str
    subject: str    # from API_KEY_TENANTS; "(unparsed)" if it could not be resolved
    role: str       # from API_KEY_ROLES; "" when the tenant default applies
    length: int


class UnrecognisedKeyConfig(ValueError):
    """``API_KEYS`` is present but in no shape this module knows.

    Raised rather than guessed. Guessing is what leaked.
    """


def _values(raw: str) -> list[str]:
    """The configured key values. Handles JSON list, JSON dict, and comma list."""
    raw = (raw or "").strip()
    if not raw:
        return []
    # A leading '[', '{' or '"' means the value is meant to be JSON. Anything that
    # then fails to decode, or decodes to a scalar, is a shape we do not know --
    # and an unknown shape must RAISE, never fall through to the comma splitter.
    # That fall-through is what turned a JSON blob into printable "subjects".
    if raw[0] in '[{"':
        try:
            obj = json.loads(raw)
        except ValueError as e:
            raise UnrecognisedKeyConfig(
                f"API_KEYS starts with {raw[0]!r} so it should be JSON, "
                f"but does not decode ({e.__class__.__name__})"
            ) from None
        if isinstance(obj, list):
            return [str(x) for x in obj]
        if isinstance(obj, dict):
            return [str(k) for k in obj]
        raise UnrecognisedKeyConfig(
            f"API_KEYS decoded to {type(obj).__name__}, not a list or dict of keys"
        )
    return [v.strip() for v in raw.split(",") if v.strip()]


def _lookup(raw: str) -> dict[str, str]:
    """A ``{key: something}`` side map. Returns {} when absent or not a dict."""
    raw = (raw or "").strip()
    if not raw.startswith("{"):
        return {}
    try:
        obj = json.loads(raw)
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in obj.items()} if isinstance(obj, dict) else {}


def summarize(env: dict[str, str]) -> list[ApiKeyInfo]:
    """Describe every configured key. Raises rather than guess an unknown shape."""
    values = _values(env.get("API_KEYS", ""))
    subjects = _lookup(env.get("API_KEY_TENANTS", ""))
    roles = _lookup(env.get("API_KEY_ROLES", ""))
    # Positional fallback ONLY for the comma-list shape, where both sides are lists.
    positional: list[str] = []
    if not subjects:
        t = (env.get("API_KEY_TENANTS", "") or "").strip()
        if t and not t.startswith("{"):
            positional = [s.strip() for s in t.split(",") if s.strip()]

    out: list[ApiKeyInfo] = []
    for i, value in enumerate(values):
        s = Secret(value)
        subject = subjects.get(value) or (positional[i] if i < len(positional) else "")
        out.append(ApiKeyInfo(
            fingerprint=s.fingerprint(),
            subject=subject or UNPARSED,
            role=roles.get(value, ""),
            length=len(value),
        ))
    return out


#: Both files are sourced by the launch scripts, and a tenant's keys may live in
#: either. hackathon keeps them in `secrets.env` (the `env normalize` layout) while
#: the others use `tenant.env`. Reading only one silently reports ZERO keys for a
#: tenant that has thirty — a false negative on a credential inventory, which is the
#: worst direction to be wrong in.
CONFIG_FILES = ("tenant.env", "secrets.env")


def tenant_env(name: str, root: str = TENANT_ROOT) -> dict[str, str]:
    """Merged config for a tenant. Later files win, matching the launch order."""
    env: dict[str, str] = {}
    for fn in CONFIG_FILES:
        f = Path(root) / name / "config" / fn
        if f.is_file():
            env.update(parse_env_file(f))
    return env


def summarize_tenant(name: str, root: str = TENANT_ROOT) -> list[ApiKeyInfo]:
    return summarize(tenant_env(name, root))


def reserved_prefix_collisions(env: dict[str, str], prefix: str = "rsk_") -> int:
    """How many configured keys use a reserved prefix (#584 §2.3).

    A count, never which — anything more identifies a key by its head.
    """
    try:
        return sum(1 for v in _values(env.get("API_KEYS", "")) if v.startswith(prefix))
    except UnrecognisedKeyConfig:
        return -1  # unknown, and the caller must not read that as "none"


def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Describe tenant API keys (never their values).")
    ap.add_argument("tenant", nargs="*")
    ap.add_argument("--root", default=TENANT_ROOT)
    ap.add_argument("--by-subject", action="store_true")
    a = ap.parse_args(argv)

    names = a.tenant or sorted(
        p.name for p in Path(a.root).iterdir()
        if any((p / "config" / f).is_file() for f in CONFIG_FILES)
    )
    for name in names:
        try:
            env = tenant_env(name, a.root)
            keys = summarize(env)
        except UnrecognisedKeyConfig as e:
            print(f"{name}: UNRECOGNISED API_KEYS shape — {e}. Nothing shown.")
            continue
        except OSError as e:
            print(f"{name}: unreadable ({e.__class__.__name__})")
            continue
        coll = reserved_prefix_collisions(env)
        roles = Counter(k.role or "(default)" for k in keys)
        warn = f"  RESERVED-PREFIX COLLISIONS: {coll}" if coll > 0 else ""
        print(f"{name}: {len(keys)} keys  roles={dict(roles)}{warn}")
        if a.by_subject:
            for subj, n in Counter(k.subject for k in keys).most_common():
                print(f"    {n:3d}  {subj}")
        else:
            for k in keys:
                print(f"    {k.fingerprint}  role={k.role or '(default)':10s} subject={k.subject}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
