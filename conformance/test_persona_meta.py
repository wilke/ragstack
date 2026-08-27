"""Meta: the P2 persona's vacuity guard actually fails when the persona is fake.

``personas.assert_persona_preconditions`` is the only thing standing between
``test_persona_p2.py`` and a suite that passes while asserting nothing — which
is precisely the failure #405 exists to remove, so shipping it un-exercised
would be the joke telling itself. Every case below takes the SAME facts that
make a genuine P2 and breaks exactly one of them, then asserts the guard says so
by name.

No server, no network, no credential: these run on every impl and in every
invocation, including ``make test-conformance-go``. That is deliberate — the
guard is a property of this suite, not of the server under test.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from personas import PersonaFacts, assert_persona_preconditions

#: A genuine P2, as observed over HTTP: the server authenticates (401 without a
#: key), the persona is not admin (403 on /v1/config), it is unconfined
#: (restricted_to null), the tenant has a pointer target `D`, P2's listing does
#: not contain `D`, and P2 owns exactly one collection of its own.
GENUINE = PersonaFacts(
    anonymous_status=401,
    p2_config_status=403,
    p2_ids=["conf-p2-mine"],
    pointer_target="conf-default",
    restricted_to=None,
    create_status=201,
)


def test_a_genuine_persona_passes() -> None:
    """The control arm. Without it, a guard that rejected EVERYTHING would make
    every case below pass."""
    assert_persona_preconditions(GENUINE)


@pytest.mark.parametrize(
    "broken,marker,why",
    [
        (
            replace(GENUINE, anonymous_status=200),
            "PRECONDITION auth_configured",
            "a keyless server makes filter_readable and enforce_access no-ops, "
            "so every P2 assertion passes vacuously",
        ),
        (
            replace(GENUINE, p2_config_status=200),
            "PRECONDITION p2_is_not_admin",
            "an admin bypasses resolve_access entirely — the branch under test "
            "is unreachable",
        ),
        (
            replace(GENUINE, p2_config_status=401),
            "PRECONDITION p2_is_not_admin",
            "an invalid key is not a persona either, and 401 must not read as "
            "'not admin, therefore fine'",
        ),
        (
            replace(GENUINE, restricted_to=["conf-p2-mine"]),
            "PRECONDITION no_tenant_allowlist",
            "a TENANT_COLLECTIONS allowlist limits the listing by CONFINEMENT, "
            "a different code path from ownership",
        ),
        (
            replace(GENUINE, restricted_to=[]),
            "PRECONDITION no_tenant_allowlist",
            "an EMPTY allowlist is still an allowlist — null is the unconfined "
            "value, and [] must not be mistaken for it",
        ),
        (
            replace(GENUINE, pointer_target=""),
            "PRECONDITION pointer_exists",
            "with no pointer target, 'P2 cannot read D' is trivially true",
        ),
        (
            replace(GENUINE, p2_ids=["conf-p2-mine", "conf-default"]),
            "PRECONDITION pointer_not_readable",
            "P2 CAN read the pointer target, so its effective default IS the "
            "pointer — exactly the state #419 could not distinguish",
        ),
        (
            replace(GENUINE, p2_ids=[], create_status=403),
            "PRECONDITION p2_has_one_collection",
            "an empty readable set is persona P3, not P2",
        ),
    ],
    ids=[
        "keyless-server",
        "persona-is-admin",
        "persona-key-invalid",
        "allowlist-present",
        "allowlist-empty-not-null",
        "no-pointer-target",
        "pointer-is-readable",
        "empty-readable-set",
    ],
)
def test_a_fake_persona_fails_by_name(
    broken: PersonaFacts, marker: str, why: str
) -> None:
    """Each violated precondition raises, and the message names it."""
    with pytest.raises(AssertionError) as excinfo:
        assert_persona_preconditions(broken)
    assert marker in str(excinfo.value), (
        f"the guard rejected these facts, but not by the expected name "
        f"({marker}) — the message a red build shows must say WHICH "
        f"precondition broke. {why}. Got: {excinfo.value}"
    )


def test_the_empty_readable_set_message_explains_a_disabled_create_switch() -> None:
    """403 on create is a *deployment* answer, not a persona answer.

    ``ALLOW_USER_COLLECTION_CREATE=false`` (#287) closes the create plane for
    non-admins, so the persona cannot be provisioned black-box on that
    deployment. The message has to say that, or the next reader spends an hour
    looking for a bug in the fixture.
    """
    with pytest.raises(AssertionError) as excinfo:
        assert_persona_preconditions(replace(GENUINE, p2_ids=[], create_status=403))
    assert "ALLOW_USER_COLLECTION_CREATE" in str(excinfo.value)


def test_the_pointer_leak_message_names_both_sides() -> None:
    """A red build must show the pointer target AND P2's listing.

    The house rule for these guards (``python/tests/conftest.py``'s import
    check, the ES opt-in, this): name the violated precondition and *both sides
    of the comparison*, so the misfire is diagnosable without a debugger.
    """
    with pytest.raises(AssertionError) as excinfo:
        assert_persona_preconditions(
            replace(GENUINE, p2_ids=["conf-p2-mine", "conf-default"])
        )
    message = str(excinfo.value)
    assert "conf-default" in message and "conf-p2-mine" in message


# --------------------------------------------------------------------------- #
# The base-url default that resolved to production (#405 / U4)
# --------------------------------------------------------------------------- #
def test_base_url_has_no_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """``RAGSTACK_BASE_URL`` is required, and its absence is a loud failure.

    It used to default to ``http://localhost:8000`` — the documented Python port
    — which on the deployment host is a live *production* API. A bare
    ``pytest conformance/`` therefore pointed a suite that creates and deletes
    collections at production. Same class as #363/#369/#392/#407/#432; the
    inventory is in ``docs/plans/README.md``.

    Calls the fixture's underlying function directly: a fixture that raises
    ``UsageError`` cannot be observed from a test that depends on it, because
    the test never starts.
    """
    import conftest

    monkeypatch.delenv("RAGSTACK_BASE_URL", raising=False)
    fn = getattr(conftest.base_url, "__wrapped__", conftest.base_url)
    with pytest.raises(pytest.UsageError) as excinfo:
        fn()
    message = str(excinfo.value)
    assert "RAGSTACK_BASE_URL" in message
    assert "make test-conformance" in message, (
        "an unset variable has to say what to run instead, or the next person "
        "just exports the old default by hand"
    )
