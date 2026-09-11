#!/usr/bin/env python
"""Generate the BV-BRC signed-token verifier fixture vectors (ADR-0007, PR-A).

Writes ``contracts/fixtures/identity/bvbrc/``:

* ``test-signing-key.pem``  — a TEST-ONLY 1024-bit RSA key (BV-BRC's own key
  size). Generated once and committed; re-runs reuse it so the vectors are
  byte-stable. Never a real key; see the sibling ``README.md``.
* ``test-wrong-key.pem``    — a second TEST-ONLY key whose only property is
  "not the signing key". Committed for the same reason: the
  ``signed_by_another_key`` vector is signed with it, and generating it fresh
  each run made that one token change on every regeneration — a diff that says
  nothing, in a file two verifiers (Python and Go) are held to.
* ``public_key.json``       — ``{"pubkey": "<PEM>"}`` exactly as the BV-BRC key
  server (``https://user.patricbrc.org/public_key``) answers.
* ``vectors.json``          — the replay table. ``python/tests/unit/
  test_identity_bvbrc_vectors.py`` replays it against
  :class:`ragstack.identity.bvbrc.BvbrcSignedToken`; the Go control plane's
  verifier (``go/internal/ctl/auth/bvbrc.go``) replays the same file, so the two
  verifiers cannot drift on the cases that matter (tampering, wrong key, the
  signed-region boundary, first-wins parsing, expiry).

The clock is pinned: ``now`` is 2025-09-11T00:00:00Z, "valid" tokens expire on
2100-01-01 and "expired" ones expired in 2023, so the file never rots. A vector
may override ``allowlist`` and ``now``; everything else is top-level.

Pure local I/O: no network, nothing read from ``~``. Usage::

    cd python && PYTHONPATH=$PWD python scripts/gen_bvbrc_vectors.py
    python scripts/gen_bvbrc_vectors.py --out /tmp/vectors --regenerate-key
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUT = _REPO / "contracts" / "fixtures" / "identity" / "bvbrc"

SCHEMA_VERSION = 1
SIGNING_SUBJECT_URL = "https://user.patricbrc.org/public_key"
#: The canonical set from BV-BRC's P3AuthConstants.pm — the default
#: IDENTITY_ISSUER_ALLOWLIST (ragstack.identity.bvbrc.DEFAULT_SIGNING_SUBJECTS).
ALLOWLIST = [
    "https://user.patricbrc.org/public_key",
    "https://user.bv-brc.org/public_key",
    "https://user.alpha.patricbrc.org/public_key",
    "https://user.beta.patricbrc.org/public_key",
]

NOW = 1757548800  # 2025-09-11T00:00:00Z
VALID_EXPIRY = 4102444800  # 2100-01-01T00:00:00Z
EXPIRED_EXPIRY = 1700000000  # 2023-11-14T22:13:20Z

ALICE = "alice@patricbrc.org"
EVE = "eve@patricbrc.org"
TOKEN_ID = "11111111-2222-3333-4444-555555555555"


def _ok(subject: str = ALICE, token_id: str = TOKEN_ID, expires_at: int = VALID_EXPIRY) -> dict:
    return {"ok": True, "subject": subject, "token_id": token_id, "expires_at": expires_at}


def _invalid(reason_substring: str) -> dict:
    return {"ok": False, "error_kind": "invalid", "reason_substring": reason_substring}


def _load_or_generate_key(path: Path, regenerate: bool):
    from cryptography.hazmat.primitives import serialization

    from tests.identity_support import generate_key

    if path.is_file() and not regenerate:
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    key = generate_key(1024)  # BV-BRC's key size; a test key, never a real one
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return key


def build_vectors(key: Any, other_key: Any) -> list[dict[str, Any]]:
    from tests.identity_support import bvbrc_payload, sign_bvbrc

    def payload(**kw: Any) -> str:
        kw.setdefault("expiry", VALID_EXPIRY)
        return bvbrc_payload(**kw)

    valid = sign_bvbrc(payload(), key)
    valid_sig_hex = valid.rpartition("|sig=")[2]

    tampered = sign_bvbrc(payload(), key).replace(f"un={ALICE}", f"un={EVE}", 1)
    assert tampered != valid and tampered.startswith(f"un={EVE}")

    vectors: list[dict[str, Any]] = [
        {"name": "valid", "token": valid, "expect": _ok()},
        {
            "name": "valid_other_allowlisted_subject",
            "token": sign_bvbrc(payload(signing_subject=ALLOWLIST[1]), key),
            # The key server for that subject serves the same test key; the
            # replay harness routes every allowlisted URL to public_key.json.
            "expect": _ok(),
        },
        {
            "name": "expired",
            "token": sign_bvbrc(payload(expiry=EXPIRED_EXPIRY), key),
            "expect": _invalid("expired"),
        },
        {
            "name": "expires_exactly_now_is_expired",
            "token": sign_bvbrc(payload(expiry=NOW), key),
            "expect": _invalid("expired"),
        },
        {
            "name": "expires_one_second_from_now_is_valid",
            "token": sign_bvbrc(payload(expiry=NOW + 1), key),
            "expect": _ok(expires_at=NOW + 1),
        },
        {
            "name": "now_override_after_expiry",
            "token": valid,
            "now": VALID_EXPIRY + 1,
            "expect": _invalid("expired"),
        },
        {
            "name": "no_expiry",
            "token": sign_bvbrc(bvbrc_payload(expiry=None), key),
            "expect": _invalid("no expiry"),
        },
        {
            "name": "non_numeric_expiry",
            "token": sign_bvbrc(
                f"un={ALICE}|tokenid={TOKEN_ID}|expiry=never|SigningSubject={SIGNING_SUBJECT_URL}",
                key,
            ),
            "expect": _invalid("not a number"),
        },
        {
            "name": "tampered_un_after_signing",
            "token": tampered,
            "expect": _invalid("does not verify"),
        },
        {
            "name": "signed_by_another_key",
            "token": sign_bvbrc(payload(), other_key),
            "expect": _invalid("does not verify"),
        },
        {
            "name": "garbage_hex_signature",
            "token": payload() + "|sig=zz-not-hex",
            "expect": _invalid("not hex"),
        },
        {
            "name": "empty_signature",
            "token": payload() + "|sig=",
            "expect": _invalid("empty signature"),
        },
        {
            "name": "no_signature_separator",
            "token": payload(),
            "expect": _invalid("no signature"),
        },
        {
            "name": "empty_payload",
            "token": "|sig=" + valid_sig_hex,
            "expect": _invalid("no signature"),
        },
        {
            "name": "signing_subject_not_in_allowlist",
            # Correctly signed by the trusted key — the pin is on the SUBJECT,
            # checked before any fetch, so this must fail without a key lookup.
            "token": sign_bvbrc(payload(signing_subject="https://evil.example.org/public_key"), key),
            "expect": _invalid("not an allowed issuer"),
        },
        {
            "name": "allowlist_override_excludes_subject",
            "token": valid,
            "allowlist": [ALLOWLIST[1]],
            "expect": _invalid("not an allowed issuer"),
        },
        {
            "name": "missing_signing_subject",
            "token": sign_bvbrc(f"un={ALICE}|tokenid={TOKEN_ID}|expiry={VALID_EXPIRY}", key),
            "expect": _invalid("not an allowed issuer"),
        },
        {
            "name": "fields_after_sig_are_ignored",
            # Outside the signed region: appending |un=eve must not change who
            # the caller is (and must not break the signature either).
            "token": valid + f"|un={EVE}",
            "expect": _ok(),
        },
        {
            "name": "duplicate_field_first_wins",
            # un=alice|…|un=eve, signed as such: the first occurrence is the
            # one a validator read, so a duplicate cannot shadow it.
            "token": sign_bvbrc(payload(extra=f"un={EVE}"), key),
            "expect": _ok(),
        },
        {
            "name": "verified_but_no_un",
            "token": sign_bvbrc(
                f"tokenid={TOKEN_ID}|expiry={VALID_EXPIRY}|SigningSubject={SIGNING_SUBJECT_URL}",
                key,
            ),
            "expect": _invalid("no un="),
        },
        {
            "name": "verified_but_no_tokenid",
            "token": sign_bvbrc(
                f"un={ALICE}|expiry={VALID_EXPIRY}|SigningSubject={SIGNING_SUBJECT_URL}", key
            ),
            "expect": _invalid("no tokenid"),
        },
    ]
    names = [v["name"] for v in vectors]
    assert len(names) == len(set(names)), "duplicate vector names"
    return vectors


def build_key_server_variants(pem: str) -> list[dict[str, Any]]:
    """Raw HTTP bodies a key server might answer with, and whether the verifier
    must be able to extract a usable key from them. ``body`` is the exact
    response text; the escaped variants carry a literal backslash-n where a
    newline belongs, which some deployments really do return."""
    escaped = pem.replace("\n", "\\n")
    return [
        {"name": "json_pubkey", "body": json.dumps({"pubkey": pem}), "expect_parse_ok": True},
        {
            "name": "json_pubkey_escaped_newlines",
            "body": json.dumps({"pubkey": escaped}),
            "expect_parse_ok": True,
        },
        {"name": "json_public_key_field", "body": json.dumps({"public_key": pem}), "expect_parse_ok": True},
        {"name": "raw_pem", "body": pem, "expect_parse_ok": True},
        {"name": "raw_pem_escaped_newlines", "body": escaped, "expect_parse_ok": True},
        {"name": "json_without_pubkey", "body": json.dumps({"key": pem}), "expect_parse_ok": False},
        {"name": "json_empty_pubkey", "body": json.dumps({"pubkey": ""}), "expect_parse_ok": False},
        {"name": "not_a_key", "body": "hello, not a PEM", "expect_parse_ok": False},
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output dir (default {DEFAULT_OUT})")
    p.add_argument(
        "--regenerate-key",
        action="store_true",
        help="generate fresh keys even if the .pem files exist (changes every vector)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # The signing helpers live in python/tests (identity_support.py); make the
    # `tests` package importable however this script was launched.
    sys.path.insert(0, str(_REPO / "python"))
    from tests.identity_support import public_pem

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    key = _load_or_generate_key(out / "test-signing-key.pem", args.regenerate_key)
    # The "wrong key" is committed too, through the same load-or-generate path:
    # its only property is "not the signing key", but generating it per run made
    # `signed_by_another_key` differ on every regeneration for no reason.
    other_key = _load_or_generate_key(out / "test-wrong-key.pem", args.regenerate_key)
    pem = public_pem(key)

    (out / "public_key.json").write_text(json.dumps({"pubkey": pem}) + "\n", encoding="utf-8")
    doc = {
        "schema_version": SCHEMA_VERSION,
        "description": (
            "BV-BRC signed-token verifier replay vectors. Signed with the TEST-ONLY key in "
            "test-signing-key.pem; public_key.json is what the key server at "
            "signing_subject_url answers. Regenerate with python/scripts/gen_bvbrc_vectors.py."
        ),
        "signing_subject_url": SIGNING_SUBJECT_URL,
        "allowlist": ALLOWLIST,
        "now": NOW,
        "vectors": build_vectors(key, other_key),
        "key_server_variants": build_key_server_variants(pem),
    }
    (out / "vectors.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {out}: {len(doc['vectors'])} vectors, "
        f"{len(doc['key_server_variants'])} key-server variants",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
