"""Describe a tenant's API keys without ever emitting a key value.

Why this exists, and why it is built the way it is: on 2026-09-17 an agent
inspecting key counts leaked live key values into a transcript TWICE — first
with a hand-rolled ``KEY=value`` parser, then with a "helpful" comma-split
fallback in the tool written to prevent the first leak. Three independent
reviews of this module's next three versions (PR #623) then each found a way
to get a key onto stdout through the ``subject`` column: a positional fallback;
map VALUES printed verbatim; a key from another tenant or a superseded
``tenant.env`` line that was not in the merged list; separator- and
case-transformed keys that defeated a windowed fragment check; hex heads
shorter than the shape rule's floor. Each fix narrowed an allowlist, and each
review found the next transformation through it.

The conclusion is structural, and it is the same one #622 reached about
sentence splitting in a biomedical corpus: an allowlist of "what a label looks
like" is a blocklist of credential shapes in disguise, and the API constrains
key format not at all (``security.py`` accepts any ASCII string), so the
blocklist can never be complete. The only design under which the rule below
is TRUE rather than defended is one that never prints a map value.

    THE ONLY THINGS THAT LEAVE THIS MODULE ARE SHA-256 PREFIXES, COUNTS, AND
    THE THREE ROLE CONSTANTS. NOTHING READ FROM A CONFIG FILE IS PRINTED.

So a subject is reported as ``sha256(label)[:12]`` — the same disclosure level
the module already accepts for the key itself — and never as text. The audit
that motivated the tool ("thirty keys share one subject") is a count per
subject-hash, which needs no name. An operator who wants to know WHICH label a
group is can offer candidates with ``--subject <label>``: the tool hashes what
the operator typed and reports the matching count. The operator's own input is
printed back; config text never is. A role is printed only if it is one of the
API's three role constants — a closed set, not a shape.

The second rule stands from the earlier rounds and is the one thing that did
hold under review:

    THIS MODULE ACCEPTS EXACTLY THE SHAPES THE API ACCEPTS, AND NOTHING ELSE.

``config.py`` parses ``API_KEYS`` as a JSON list of strings and
``API_KEY_TENANTS`` / ``API_KEY_ROLES`` as JSON ``{key: label}`` dicts; a comma
list or a dict for ``API_KEYS`` cannot start the API. Anything else RAISES,
with a message that names the setting and the shape — never the value.

One deliberate, documented exception to "nothing value-derived but hashes":
:func:`reserved_prefix_collisions` reports how MANY keys begin with the
``rsk_`` prefix that #584 reserves for user keys. That is one bit per key, by
design — #584 §2.3 requires startup to refuse env keys using the user prefix,
and a count is how an operator finds them. Note ``rsk_`` is not yet minted
anywhere in this tree (it is #584's plan); the count is forward-looking.
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
UNMAPPED = "(unmapped)"          # the key has no entry in API_KEY_TENANTS
UNRECOGNISED = "(unrecognised)"  # a role that is not one of the API's three

#: The API's roles (``ragstack.api.security.ROLE_*``). Spelled out rather than
#: imported so ``ops`` does not depend on the API layer; ``test_tenant_keys``
#: asserts this set equals the security module's constants. A CLOSED set of
#: three known strings is the only allowlist this module keeps.
KNOWN_ROLES: frozenset[str] = frozenset({"admin", "user", "researcher"})

FINGERPRINT_LEN = 12


def fingerprint(value: str, n: int = FINGERPRINT_LEN) -> str:
    """``sha256(value)[:n]`` — the one shape of anything value-derived here."""
    return hashlib.sha256(value.encode()).hexdigest()[:n]


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

    def fingerprint(self, n: int = FINGERPRINT_LEN) -> str:
        return fingerprint(self._v, n)

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
    """One configured key. Every field is a hash, a marker, or a role constant."""

    fingerprint: str      # sha256(key)[:12]
    subject: str          # sha256(label)[:12], or "(unmapped)"
    role: str             # one of KNOWN_ROLES, "" for the tenant default, or "(unrecognised)"


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
    except (ValueError, RecursionError) as e:
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


def _role(label: str | None) -> str:
    if label is None:
        return ""
    return label if label in KNOWN_ROLES else UNRECOGNISED


def summarize(env: dict[str, str]) -> list[ApiKeyInfo]:
    """Describe every configured key. Raises on any shape the API would refuse.

    The subject is HASHED, never returned as text. See the module docstring for
    why three rounds of allowlisting could not make printing it safe.
    """
    keys = _keys(env)
    subjects = _map(env, "API_KEY_TENANTS")
    roles = _map(env, "API_KEY_ROLES")
    out = []
    for k in keys:
        label = subjects.get(k)
        out.append(ApiKeyInfo(
            fingerprint=Secret(k).fingerprint(),
            subject=UNMAPPED if label is None else fingerprint(label),
            role=_role(roles.get(k)),
        ))
    return out


def subject_groups(infos: list[ApiKeyInfo]) -> Counter[str]:
    """Keys per subject-hash — the "thirty keys share one subject" audit, nameless."""
    return Counter(i.subject for i in infos)


def resolve_subjects(infos: list[ApiKeyInfo], candidates: list[str]) -> dict[str, int]:
    """For each candidate label the OPERATOR supplied, how many keys carry it.

    The candidate is hashed and matched against the summaries; the candidate
    text is the operator's own input and may be printed back. Config text is
    never consulted for the label.
    """
    groups = subject_groups(infos)
    return {c: groups.get(fingerprint(c), 0) for c in candidates}


#: Both files are sourced by the launch scripts, and a tenant's keys may live in
#: either. hackathon keeps them in `secrets.env` (the `env normalize` layout) while
#: the others use `tenant.env`. Reading only one silently reports ZERO keys for a
#: tenant that has thirty — a false negative on a credential inventory, which is the
#: worst direction to be wrong in. Later files win, matching the launch order.
CONFIG_FILES = ("tenant.env", "secrets.env")


def tenant_files(name: str, root: str = TENANT_ROOT) -> list[Path]:
    return [f for fn in CONFIG_FILES if (f := Path(root) / name / "config" / fn).is_file()]


def tenant_env(name: str, root: str = TENANT_ROOT) -> dict[str, str]:
    """Merged config for a tenant. Later files win, matching the launch order."""
    env: dict[str, str] = {}
    for f in tenant_files(name, root):
        env.update(parse_env_file(f))
    return env


def summarize_tenant(name: str, root: str = TENANT_ROOT) -> list[ApiKeyInfo]:
    return summarize(tenant_env(name, root))


def reserved_prefix_collisions(env: dict[str, str], prefix: str = "rsk_") -> int:
    """How many configured keys use a reserved prefix (#584 §2.3).

    A count, never which — anything more identifies a key by its head. This is
    the module's one deliberate value-derived non-hash: one bit per key, because
    #584 requires startup to refuse env keys that use the user-key prefix and a
    count is how an operator finds them. Raises on an unreadable shape: an
    earlier version returned ``-1`` for "cannot tell", and ``-1`` is truthy,
    ``< 1`` and sums into a total — every way a caller can misread it.
    """
    return sum(1 for k in _keys(env) if k.startswith(prefix))


def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Describe tenant API keys: fingerprints, subject-hash groups, roles. "
                    "Never a key value, never a label from a config file.")
    ap.add_argument("tenant", nargs="*")
    ap.add_argument("--root", default=TENANT_ROOT)
    ap.add_argument("--subject", action="append", default=[], metavar="LABEL",
                    help="a candidate subject label (your own input); reports how many keys "
                         "carry it, by hash match. Repeatable.")
    a = ap.parse_args(argv)

    root = Path(a.root)
    if not root.is_dir():
        print(f"no such tenant root: {root}")
        return 1
    names = a.tenant or sorted(
        p.name for p in root.iterdir()
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
        except (OSError, UnicodeDecodeError, ValueError, RecursionError) as e:
            # One line naming the exception class, never a value; on to the next
            # tenant rather than aborting the listing.
            print(f"{name}: unreadable ({e.__class__.__name__})")
            continue
        roles = Counter(k.role or "(default)" for k in keys)
        warn = f"  RESERVED-PREFIX COLLISIONS: {coll}" if coll > 0 else ""
        groups = subject_groups(keys)
        print(f"{name}: {len(keys)} keys  roles={dict(roles)}  subjects={len(groups)} distinct{warn}")
        for subj, n in groups.most_common():
            print(f"    {n:3d}  subject={subj}")
        if a.subject:
            for label, n in resolve_subjects(keys, a.subject).items():
                print(f"    --subject {label!r}: {n} keys")
        for k in keys:
            print(f"      {k.fingerprint}  role={k.role or '(default)':14s} subject={k.subject}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
