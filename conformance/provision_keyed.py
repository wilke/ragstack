"""Make the registry pointer's target PRIVATE, so a P2 persona can exist (#405).

``run_authz_keyed.sh`` boots an in-memory server whose registry pointer names the
settings-derived collection. That entry has no recorded owner, so the startup ACL
backfill treats it as a legacy corpus and grants it ``read`` to the built-in
``public`` group (``api/access.py``, ADR-0004 decision 4). World-readable is the
right default for a legacy shared surface — and it makes the P2 persona
impossible: *every* principal can read the pointer target, so nobody's readable
set can exclude it, so the branch #419 lived in stays unreachable and the whole
point of #405 is lost.

This script revokes that one grant, as admin, over HTTP, and then **verifies from
the restricted principal's own point of view** that the pointer target is gone
from its listing. A provisioning step that reports success without checking the
effect is how a suite ends up asserting nothing.

Two topologies were tried before this one, and both are worse:

* pin ``DEFAULT_COLLECTION_ID`` at a collection created after boot — impossible:
  the pointer is resolved at boot and an unresolvable value is fatal
  (``deps.py::_resolve_default_id``);
* register the pointer target from a ``collections_file`` with an explicit
  ``owner`` so it is private from the start — this works, but every registry-spec
  collection is built with a **Qdrant** store regardless of ``VECTOR_BACKEND``
  (``deps.py::build_collection_entry`` — #451), so the in-memory run then had a
  dead store under its default collection and a third of the suite 503'd.

It also grants the *unrestricted* non-admin an explicit read share on the same
collection, which restores what the public grant used to give it. That is not a
convenience: it is what makes the two principals the matrix's **P1** ("owns what
it uses; **can** read the collection the registry pointer names") and **P2**
("exactly one collection, which is **not** the pointer's target"). Without it
both keys are restricted, the contrast the suite is built on disappears, and the
pre-existing authz assertions that need a readable default start 404ing.

Usage::

    python provision_keyed.py <base_url> <admin_key> <p1_key> <p1_subject> <p2_key>

Prints the pointer target id on success; exits non-zero, explaining, otherwise.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

PUBLIC_GROUP = "public"


def _call(
    method: str, url: str, key: str, body: dict | None = None
) -> tuple[int, object]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"X-API-Key": key}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, body.decode("utf-8", "replace")


def _visible(base: str, key: str, who: str) -> list[str]:
    status, listing = _call("GET", f"{base}/v1/collections", key)
    if status != 200 or not isinstance(listing, dict):
        fail(f"{who}: GET /v1/collections returned {status}: {listing!r}")
    assert isinstance(listing, dict)  # for type checkers; fail() raises above
    return [c["id"] for c in listing["collections"]]


def fail(msg: str) -> None:
    print(f"[provision] ABORT: {msg}", file=sys.stderr)
    raise SystemExit(1)


def main(argv: list[str]) -> int:
    base = argv[1].rstrip("/")
    admin_key, p1_key, p1_subject, p2_key = argv[2], argv[3], argv[4], argv[5]

    status, listing = _call("GET", f"{base}/v1/collections", admin_key)
    if status != 200 or not isinstance(listing, dict):
        fail(f"admin GET /v1/collections returned {status}: {listing!r}")
    pointer = listing["default"]
    if not pointer:
        fail(
            "the admin listing reports default='' — there is no registry pointer "
            "target, so there is nothing for the persona to be excluded from"
        )

    status, shares = _call("GET", f"{base}/v1/collections/{pointer}/shares", admin_key)
    if status != 200 or not isinstance(shares, dict):
        fail(f"GET .../{pointer}/shares returned {status}: {shares!r}")

    revoked = 0
    for share in shares["shares"]:
        if (
            share["active"]
            and share["grantee_type"] == "group"
            and share["grantee_id"] == PUBLIC_GROUP
        ):
            status, body = _call(
                "DELETE",
                f"{base}/v1/collections/{pointer}/shares/{share['id']}",
                admin_key,
            )
            if status != 204:
                fail(f"revoking the public grant returned {status}: {body!r}")
            revoked += 1

    # P1: an explicit read share, replacing what `public` used to give it.
    # `@service:` keeps the subject colon-free and verbatim; a bare name would be
    # qualified to `bvbrc:<name>` and grant read to a subject that can never
    # authenticate — an inert grant that looks like a successful one.
    status, granted = _call(
        "POST",
        f"{base}/v1/collections/{pointer}/shares",
        admin_key,
        {"grantee": f"@service:{p1_subject}", "permission": "read"},
    )
    if status != 201 or not isinstance(granted, dict):
        fail(f"granting P1 read on {pointer!r} returned {status}: {granted!r}")
    if granted["grantee_id"] != p1_subject:
        fail(
            f"the share resolved to grantee {granted['grantee_id']!r}, not "
            f"{p1_subject!r} — the API-key tenant it must match. A grant to a "
            "subject that never authenticates is inert, and reads as success."
        )

    # The checks that matter: ask each principal what IT can see. A 204 and a 201
    # say rows changed; only these say the two personas are real.
    p1_visible = _visible(base, p1_key, "P1 (unrestricted non-admin)")
    p2_visible = _visible(base, p2_key, "P2 (restricted non-admin)")
    if pointer not in p1_visible:
        fail(
            f"P1 cannot read the pointer target {pointer!r} (its listing: "
            f"{p1_visible}). P1 is defined as the caller who CAN — with both "
            "principals restricted there is no contrast left to test."
        )
    if pointer in p2_visible:
        fail(
            f"P2 can still read the pointer target {pointer!r} (its listing: "
            f"{p2_visible}). {revoked} public grant(s) were revoked, so "
            "something else grants read — another share, or a second public "
            "row. The P2 persona would be vacuous."
        )

    print(
        f"[provision] pointer target {pointer!r}: {revoked} public grant(s) "
        f"revoked, read granted to P1. P1 sees {p1_visible}; P2 sees {p2_visible}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
