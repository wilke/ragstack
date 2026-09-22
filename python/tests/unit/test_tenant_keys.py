"""`ops.tenant_keys` — describe API keys without ever emitting one.

Written after a real disclosure, twice over on the same day. First a hand-rolled
`KEY=value` parser assumed `API_KEYS` was a comma list. Then the tool written to
prevent that assumed it was a JSON *dict* — it is a JSON *list*, while
`API_KEY_TENANTS` and `API_KEY_ROLES` are dicts KEYED BY the secret — and its
fallback split the raw value on commas and printed the fragments.

So these tests pin the structural property, not the counts: no value-derived
string other than a hash prefix can leave this module, and an unrecognised shape
raises instead of guessing. The parametrised `REAL_SHAPE` case is the one that
was missed; it is first for a reason.
"""
from __future__ import annotations

import json

import pytest

from ragstack.ops.tenant_keys import (
    ApiKeyInfo,
    Secret,
    UnrecognisedKeyConfig,
    reserved_prefix_collisions,
    summarize,
)

K1 = "rk-asm-ro01-SUPERSECRETVALUE123456"
K2 = "aweb_DEADBEEFCAFEBABE0123456789"

#: The shape the live tenants actually use: API_KEYS a LIST, the side maps DICTS.
REAL_SHAPE = {
    "API_KEYS": json.dumps([K1, K2]),
    "API_KEY_TENANTS": json.dumps({K1: "asm-ro", K2: "svc-asm-web"}),
    "API_KEY_ROLES": json.dumps({K1: "user", K2: "admin"}),
}
DICT_SHAPE = {
    "API_KEYS": json.dumps({K1: "asm-ro", K2: "svc-asm-web"}),
    "API_KEY_TENANTS": json.dumps({K1: "asm-ro", K2: "svc-asm-web"}),
    "API_KEY_ROLES": json.dumps({K1: "user", K2: "admin"}),
}
COMMA_SHAPE = {"API_KEYS": f"{K1},{K2}", "API_KEY_TENANTS": "asm-ro,svc-asm-web"}
ALL_SHAPES = [REAL_SHAPE, DICT_SHAPE, COMMA_SHAPE]
IDS = ["json-list (the real one)", "json-dict", "comma-list"]


# --- the structural property ---------------------------------------------------

@pytest.mark.parametrize("env", ALL_SHAPES, ids=IDS)
def test_no_key_value_or_fragment_of_one_reaches_a_summary(env) -> None:
    """The canary: every field, rendered every way, for every supported shape."""
    infos = summarize(env)
    blob = repr(infos) + json.dumps([i.__dict__ for i in infos], default=str)
    for secret in (K1, K2):
        assert secret not in blob
        for n in (8, 12, 16):           # not even a usable prefix of one
            assert secret[:n] not in blob


@pytest.mark.parametrize("render", [repr, str, "{}".format, lambda s: f"{s}"])
def test_a_secret_never_renders_itself(render) -> None:
    assert K1 not in render(Secret(K1))
    assert "redacted" in render(Secret(K1))


def test_a_secret_survives_json_default_str() -> None:
    assert K1 not in json.dumps({"key": Secret(K1)}, default=str)


def test_reveal_is_the_only_way_out() -> None:
    assert Secret(K1).reveal() == K1


def test_an_unrecognised_shape_raises_rather_than_guessing() -> None:
    """Guessing is what leaked: a bad guess turned secrets into printable text."""
    with pytest.raises(UnrecognisedKeyConfig):
        summarize({"API_KEYS": '["unterminated'})
    with pytest.raises(UnrecognisedKeyConfig):
        summarize({"API_KEYS": '"just a string"'})


def test_the_exception_message_carries_no_key_material() -> None:
    try:
        summarize({"API_KEYS": '[' + json.dumps(K1) + ',,]'})
    except UnrecognisedKeyConfig as e:
        assert K1 not in str(e) and K1[:8] not in str(e)
    else:
        pytest.fail("expected UnrecognisedKeyConfig")


# --- and it still has to be useful ---------------------------------------------

@pytest.mark.parametrize("env", ALL_SHAPES, ids=IDS)
def test_every_shape_resolves_the_same_two_subjects(env) -> None:
    assert {i.subject for i in summarize(env)} == {"asm-ro", "svc-asm-web"}


@pytest.mark.parametrize("env", [REAL_SHAPE, DICT_SHAPE], ids=["json-list", "json-dict"])
def test_roles_are_resolved_when_the_side_map_is_present(env) -> None:
    assert {i.role for i in summarize(env)} == {"user", "admin"}


def test_a_key_has_the_same_fingerprint_however_the_file_spells_the_config() -> None:
    by_subject = lambda env: {i.subject: i.fingerprint for i in summarize(env)}  # noqa: E731
    assert by_subject(REAL_SHAPE) == by_subject(DICT_SHAPE) == by_subject(COMMA_SHAPE)


def test_absent_config_is_empty_not_an_error() -> None:
    assert summarize({}) == []
    assert summarize({"API_KEYS": ""}) == []


def test_reserved_prefix_collisions_counts_without_naming() -> None:
    """#584 section 2.3: startup must refuse an env key using the user-key prefix."""
    assert reserved_prefix_collisions(REAL_SHAPE) == 0
    assert reserved_prefix_collisions({"API_KEYS": json.dumps(["rsk_x", K1])}) == 1


def test_an_unreadable_shape_reports_unknown_not_zero_collisions() -> None:
    """-1, because a caller must not read 'cannot tell' as 'none'."""
    assert reserved_prefix_collisions({"API_KEYS": '["unterminated'}) == -1


def test_apikeyinfo_carries_only_a_hash() -> None:
    i = ApiKeyInfo(fingerprint="abc123456789", subject="s", role="user", length=40)
    assert not hasattr(i, "prefix")   # a key head is a partial disclosure; removed
    assert K1 not in repr(i)


def test_a_tenant_whose_keys_live_in_secrets_env_is_not_reported_as_zero(tmp_path) -> None:
    """The false negative that actually happened: hackathon keeps its keys in
    `secrets.env`, and a reader that opened only `tenant.env` reported 0 keys for
    a tenant holding thirty. On a credential inventory, under-reporting is the
    dangerous direction."""
    from ragstack.ops.tenant_keys import summarize_tenant

    cfg = tmp_path / "t1" / "config"
    cfg.mkdir(parents=True)
    (cfg / "tenant.env").write_text("QDRANT_URL=http://x\n")
    (cfg / "secrets.env").write_text(
        f"API_KEYS='{json.dumps([K1])}'\nAPI_KEY_TENANTS='{json.dumps({K1: 'a-01'})}'\n"
    )
    infos = summarize_tenant("t1", root=str(tmp_path))
    assert [i.subject for i in infos] == ["a-01"]
    assert K1 not in repr(infos)
