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

#: A label is a FRAGMENT of a key if any window of this many characters of it
#: occurs inside any key the run knows about (shorter labels: if the whole label
#: occurs inside a key). Whole-containment — the first rewrite's rule — let a
#: label that overlapped a key by 20 characters through because neither was a
#: substring of the other; the canary refuses prefixes down to 4 characters, so
#: the module's rule has to be at least that strict for short labels.
_FRAGMENT_LEN = 8
#: KNOWN LIMITATION, accepted: under a key scheme that embeds the subject in the
#: key (``rk-<subject>-<random>``), the subject is a substring of its own key and
#: is suppressed as ``(unrecognised)``. This host's ctl mints ``token_hex(32)``
#: keys, in which no real subject can occur (they contain non-hex characters), so
#: the rule never fires on real names here — pinned by
#: ``test_real_subject_forms_on_this_host_are_never_suppressed``. If the key
#: format ever changes to embed subjects, this rule must change with it.

#: What a CREDENTIAL looks like, independent of any key the run can see: the ctl's
#: ``key mint`` emits ``token_hex(32)`` (64 hex chars, ``go/internal/ctl/ops/creds.go``)
#: and the quickstart's ``openssl rand -hex 32`` is the same shape; user keys carry
#: the reserved ``rsk_`` prefix (#584). No subject on this host is hex-only. This is
#: the rule for the case the fragment check cannot cover — a key from a tenant
#: OUTSIDE the run pasted into a map value — and it is why a 64-character label
#: bound alone was not enough: a 64-hex key IS a syntactically valid label.
_HEXISH = re.compile(r"^[0-9a-fA-F]{32,}$")
_RESERVED_PREFIXES = ("rsk_",)


def _looks_like_a_credential(label: str) -> bool:
    return bool(_HEXISH.match(label)) or label.startswith(_RESERVED_PREFIXES)


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
    """Does any ``_FRAGMENT_LEN``-char window of ``label`` occur inside any key?

    Shorter labels: does the whole label occur inside any key. Windows, not
    containment: ``label in k or k in label`` missed a label that overlapped a
    key by twenty characters with a decoration on each end.
    """
    ks = [k for k in keys if k]
    if not label:
        return False
    if len(label) < _FRAGMENT_LEN:
        return any(label in k for k in ks)
    windows = {label[i:i + _FRAGMENT_LEN] for i in range(len(label) - _FRAGMENT_LEN + 1)}
    return any(w in k for k in ks for w in windows)


def _safe_subject(label: str | None, known_keys: Iterable[str]) -> str:
    """The label if it is printable, else a marker. ``known_keys`` is every key
    value the RUN has seen — every file of every tenant, read before any merge —
    not just this tenant's final list. See :func:`key_material` for why."""
    if label is None:
        return UNMAPPED
    if _LABEL.match(label) and not _looks_like_a_credential(label) \
            and not _is_fragment(label, known_keys):
        return label
    return UNRECOGNISED


def _safe_role(label: str | None) -> str:
    if label is None:
        return ""
    return label if label in KNOWN_ROLES else UNRECOGNISED


def summarize(env: dict[str, str], known_keys: Iterable[str] = ()) -> list[ApiKeyInfo]:
    """Describe every configured key. Raises on any shape the API would refuse.

    ``known_keys`` widens the allowlist's denominator beyond this env's own
    ``API_KEYS`` — pass :func:`key_material` of every file in the run. Without it
    a key that is NOT in the final merged list (rotation residue in ``tenant.env``
    overridden by ``secrets.env``; a key pasted from another tenant into a map
    value) is not a fragment of anything and, being 64 hex characters, is a
    syntactically valid label. That is how the second rewrite leaked.
    """
    keys = _keys(env)
    subjects = _map(env, "API_KEY_TENANTS")
    roles = _map(env, "API_KEY_ROLES")
    known = set(keys) | set(known_keys)
    return [
        ApiKeyInfo(
            fingerprint=Secret(k).fingerprint(),
            subject=_safe_subject(subjects.get(k), known),
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


_KEY_SETTINGS = ("API_KEYS", "API_KEY_TENANTS", "API_KEY_ROLES")


def tenant_files(name: str, root: str = TENANT_ROOT) -> list[Path]:
    return [f for fn in CONFIG_FILES if (f := Path(root) / name / "config" / fn).is_file()]


def tenant_env(name: str, root: str = TENANT_ROOT) -> dict[str, str]:
    """Merged config for a tenant. Later files win, matching the launch order."""
    env: dict[str, str] = {}
    for f in tenant_files(name, root):
        env.update(parse_env_file(f))
    return env


def key_material(files: Iterable[Path]) -> set[str]:
    """Every string that could be a key, from every file read INDIVIDUALLY.

    This is the allowlist's *denominator*: each ``API_KEYS`` entry (and the raw
    ``API_KEYS`` text, which holds only keys, so an undecodable value still
    counts), plus every side-map dict KEY — the maps are keyed by the secret.

    NOT the maps' values, and NOT the maps' raw text: those contain the labels,
    and a label that is in the denominator is a fragment of itself — the first
    draft of this function suppressed every legitimate subject that way. An
    inverted map's values ARE keys, but they are keys the run already knows
    from some ``API_KEYS`` list, or they are refused by shape
    (:func:`_looks_like_a_credential`); the map itself fails the shape check
    if it is not ``str→str``.

    A file or setting that cannot be read contributes nothing — ``summarize``
    will say so for that tenant. Read pre-merge: the merged view is exactly
    what hides a superseded key still sitting in ``tenant.env``.
    """
    found: set[str] = set()
    for f in files:
        try:
            env = parse_env_file(f)
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        for name in _KEY_SETTINGS:
            raw = (env.get(name, "") or "").strip()
            if not raw:
                continue
            if name == "API_KEYS":
                found.add(raw)
            try:
                obj = json.loads(raw)
            except (ValueError, RecursionError):
                continue
            if name == "API_KEYS" and isinstance(obj, list):
                found.update(str(x) for x in obj)
            elif name != "API_KEYS" and isinstance(obj, dict):
                found.update(str(k) for k in obj)
    return {x for x in found if x}


def summarize_tenant(name: str, root: str = TENANT_ROOT,
                     known_keys: Iterable[str] = ()) -> list[ApiKeyInfo]:
    files = tenant_files(name, root)
    return summarize(tenant_env(name, root), set(known_keys) | key_material(files))


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

    root = Path(a.root)
    if not root.is_dir():
        print(f"no such tenant root: {root}")
        return 1
    names = a.tenant or sorted(
        p.name for p in root.iterdir()
        if any((p / "config" / f).is_file() for f in CONFIG_FILES)
    )
    # Pass 1: every key value the whole run can see, before any merge. A key that
    # one tenant lists and another tenant's map carries as a VALUE is only
    # suppressible if the second tenant's allowlist knows the first tenant's key.
    known: set[str] = set()
    for name in names:
        known |= key_material(tenant_files(name, a.root))
    for name in names:
        try:
            env = tenant_env(name, a.root)
            keys = summarize(env, known)
            coll = reserved_prefix_collisions(env)
        except UnrecognisedKeyConfig as e:
            # The message names the setting and shape; it never carries a value.
            print(f"{name}: UNRECOGNISED key config — {e}")
            continue
        except (OSError, UnicodeDecodeError, ValueError, RecursionError) as e:
            # parse_env_file swallows shlex's own errors and keeps the raw text, so
            # ValueError here is defence in depth rather than a live path;
            # RecursionError is json.loads on a pathologically nested value.
            # Either way: one line naming the class, never a value, and on to the
            # next tenant.
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
