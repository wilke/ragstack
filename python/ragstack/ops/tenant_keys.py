"""Describe a tenant's API keys without ever emitting a key value.

Why this exists, and why it is built the way it is: on 2026-09-17 an agent
inspecting key counts leaked live key values into a transcript TWICE. First by
hand-rolling a ``KEY=value`` parser instead of using :func:`parse_env_file`.
Then — while writing this module to prevent that — by giving it a "helpful"
fallback: when JSON parsing failed it split the raw value on commas and rendered
the fragments, which are secrets, into a field it printed.

And then a THIRD time, in review of this module's first version (PR #623): the
positional comma-split had survived as a fallback for ``API_KEY_TENANTS``, and
the side maps' *values* were printed verbatim as ``subject`` and ``role`` — so a
mis-keyed, inverted or editor-wrapped map printed secrets. The canary never
looked at stdout, and every test shape mapped every key, so the paths where the
leaks lived were never exercised.

The rule is therefore structural, and now has three parts:

    1. THE ONLY VALUE-DERIVED FIELD THAT LEAVES THIS MODULE IS A SHA-256 PREFIX.
    2. THIS MODULE ACCEPTS EXACTLY THE SHAPES THE API ACCEPTS, AND NOTHING ELSE.
    3. NOTHING READ FROM A CONFIG FILE IS PRINTED UNLESS IT PASSES AN ALLOWLIST.

(2) is what removes the leak paths rather than guarding them. ``config.py``
parses ``API_KEYS`` as a JSON **list** of strings and ``API_KEY_TENANTS`` /
``API_KEY_ROLES`` as JSON **dicts** ``{key: label}``; a comma list or a dict for
``API_KEYS`` cannot start the API (``SettingsError`` / ``ValidationError``). So a
parser here that "helpfully" accepted them was describing deployments that
cannot exist — and those branches were exactly where the leaks were. Any other
shape RAISES. Guessing is what leaked.

(3) is what makes (1) true even when (2) is satisfied: a dict is the right shape
and still leaks if its values are secrets (an inverted ``{subject: key}`` map is
a one-line editing mistake away). A subject is printed only if it looks like a
label AND is not a fragment of any configured key; a role only if it is one of
the API's known roles. Anything else prints as ``(unrecognised)`` beside the
fingerprint — which is all a credential inventory needs.

There is no ``length`` field and ``Secret`` has no ``__len__``: a key's length
is value-derived and the rule says *only* the hash prefix.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ragstack.ops.store_inventory import parse_env_file

TENANT_ROOT = "/rag/data/tenants"
REDACTED = "<redacted>"
UNMAPPED = "(unmapped)"          # the key has no entry in API_KEY_TENANTS
UNRECOGNISED = "(unrecognised)"  # the map had an entry, and it was not a printable label

#: The API's roles (``ragstack.api.security.ROLE_*``). Spelled out rather than
#: imported so ``ops`` does not depend on the API layer; ``test_tenant_keys``
#: asserts this set equals the security module's constants.
KNOWN_ROLES: frozenset[str] = frozenset({"admin", "user", "researcher"})

#: What a subject LABEL looks like: ``asm-ro``, ``svc-asm-web``, ``hackathon-a-01``,
#: ``bvbrc:alice@patricbrc.org``. Bounded, no whitespace, no quotes, no braces,
#: no ``$``/backticks — nothing a shell or JSON fragment would carry.
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,63}$")

#: A label that shares this many characters with any configured key is treated
#: as a fragment of one, whatever it looks like. 8 is the shortest prefix the
#: canary tests refuse; a label shorter than this cannot be a useful fragment.
_FRAGMENT_LEN = 8


class Secret:
    """A string that will not print itself.

    ``repr``, ``str`` and ``format`` render ``<redacted>``, so a Secret is safe in
    an f-string, a log line, a traceback and ``json.dumps(..., default=str)``.
    Equality and hashing use the real value so it remains usable as a dict key.
    It refuses to pickle: ``pickle.dumps`` would write the value in plaintext.
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
    def __reduce__(self) -> tuple:  # type: ignore[override]
        raise TypeError("Secret does not pickle: that would write the value in plaintext")
    def __getstate__(self) -> None:
        raise TypeError("Secret does not pickle: that would write the value in plaintext")


@dataclass(frozen=True)
class ApiKeyInfo:
    """One configured key. ``fingerprint`` is the only value-derived field."""

    fingerprint: str
    subject: str    # an allowlisted label, or "(unmapped)" / "(unrecognised)"
    role: str       # one of KNOWN_ROLES, "" for the tenant default, or "(unrecognised)"


class UnrecognisedKeyConfig(ValueError):
    """A key setting is present but in no shape the API accepts.

    Raised rather than guessed. Guessing is what leaked. The message names the
    setting and the shape seen; it never carries the value.
    """


def _decode(name: str, raw: str) -> object | None:
    """JSON-decode ``raw`` or raise. ``None`` for absent/empty (the API's default)."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError as e:
        raise UnrecognisedKeyConfig(
            f"{name} is not valid JSON ({e.__class__.__name__}); the API would refuse "
            f"to start on it. Nothing shown."
        ) from None


def _keys(env: dict[str, str]) -> list[str]:
    """``API_KEYS`` as the API reads it: a JSON list of strings. Anything else raises."""
    obj = _decode("API_KEYS", env.get("API_KEYS", ""))
    if obj is None:
        return []
    if isinstance(obj, list) and all(isinstance(x, str) for x in obj):
        return obj
    kind = type(obj).__name__ if not isinstance(obj, list) else "list with non-string items"
    raise UnrecognisedKeyConfig(f"API_KEYS decoded to {kind}; the API accepts only a list of strings")


def _map(env: dict[str, str], name: str) -> dict[str, str]:
    """A ``{key: label}`` side map as the API reads it. Absent → {}; wrong shape raises."""
    obj = _decode(name, env.get(name, ""))
    if obj is None:
        return {}
    if isinstance(obj, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in obj.items()):
        return obj
    kind = type(obj).__name__ if not isinstance(obj, dict) else "dict with non-string entries"
    raise UnrecognisedKeyConfig(f"{name} decoded to {kind}; the API accepts only a string→string dict")


def _is_fragment(label: str, keys: Iterable[str]) -> bool:
    """Does ``label`` share ≥ _FRAGMENT_LEN characters with any key, either way round?"""
    if len(label) < _FRAGMENT_LEN:
        # Too short to be a usable fragment — but never a key itself.
        return any(label == k for k in keys)
    return any(label in k or k in label for k in keys)


def _safe_subject(label: str | None, keys: list[str]) -> str:
    if label is None:
        return UNMAPPED
    if _LABEL.match(label) and not _is_fragment(label, keys):
        return label
    return UNRECOGNISED


def _safe_role(label: str | None) -> str:
    if label is None:
        return ""
    return label if label in KNOWN_ROLES else UNRECOGNISED


def summarize(env: dict[str, str]) -> list[ApiKeyInfo]:
    """Describe every configured key. Raises on any shape the API would refuse."""
    keys = _keys(env)
    subjects = _map(env, "API_KEY_TENANTS")
    roles = _map(env, "API_KEY_ROLES")
    return [
        ApiKeyInfo(
            fingerprint=Secret(k).fingerprint(),
            subject=_safe_subject(subjects.get(k), keys),
            role=_safe_role(roles.get(k)),
        )
        for k in keys
    ]


#: Both files are sourced by the launch scripts, and a tenant's keys may live in
#: either. hackathon keeps them in `secrets.env` (the `env normalize` layout) while
#: the others use `tenant.env`. Reading only one silently reports ZERO keys for a
#: tenant that has thirty — a false negative on a credential inventory, which is the
#: worst direction to be wrong in. Later files win, matching the launch order.
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

    A count, never which — anything more identifies a key by its head. Raises
    :class:`UnrecognisedKeyConfig` on an unreadable shape: the first version
    returned ``-1`` for "cannot tell", and ``-1`` is truthy, ``< 1`` and sums into
    a total — every way a caller can misread "unknown" as a number.
    """
    return sum(1 for k in _keys(env) if k.startswith(prefix))


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
            coll = reserved_prefix_collisions(env)
        except UnrecognisedKeyConfig as e:
            # The message names the setting and shape; it never carries a value.
            print(f"{name}: UNRECOGNISED key config — {e}")
            continue
        except (OSError, UnicodeDecodeError, ValueError) as e:
            # ValueError: shlex refusing an unbalanced quote in parse_env_file. Its
            # message names the problem, never the value.
            print(f"{name}: unreadable ({e.__class__.__name__})")
            continue
        roles = Counter(k.role or "(default)" for k in keys)
        warn = f"  RESERVED-PREFIX COLLISIONS: {coll}" if coll > 0 else ""
        print(f"{name}: {len(keys)} keys  roles={dict(roles)}{warn}")
        if a.by_subject:
            for subj, n in Counter(k.subject for k in keys).most_common():
                print(f"    {n:3d}  {subj}")
        else:
            for k in keys:
                print(f"    {k.fingerprint}  role={k.role or '(default)':14s} subject={k.subject}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
