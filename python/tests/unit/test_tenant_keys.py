"""`ops.tenant_keys` — describe API keys without ever emitting one.

Written after a real disclosure, twice over on the same day, and then a third
time in review of this module's first version: a positional comma-split had
survived as a fallback, the side maps' VALUES were printed verbatim as
subject/role, and the canary only inspected the return value while every test
shape mapped every key — so the leak paths were never exercised.

So the canary here is the CLI's actual stdout+stderr, over configs whose map
values and raw text CONTAIN the marker: inverted maps, a list where a dict
belongs, a pretty-printed dict that `parse_env_file` cannot balance, shell
metacharacters, a newline, an unmapped key. If a single character of a fake
secret — or any 4/6/8/12/16-char prefix of one — reaches output, the structural
claim is false. The shapes the API refuses to start on must RAISE here, not
parse: a parser that accepts what the API rejects is describing a deployment
that cannot exist, and that is where the leaks lived.
"""
from __future__ import annotations

import json
import pickle

import pytest

from ragstack.ops.tenant_keys import (
    KNOWN_ROLES,
    UNMAPPED,
    UNRECOGNISED,
    ApiKeyInfo,
    Secret,
    UnrecognisedKeyConfig,
    _main,
    reserved_prefix_collisions,
    summarize,
)

MARK = "ZQMARKERQZ"
K1 = f"rk-asm-ro01-{MARK}-SUPERSECRETVALUE123456"
K2 = f"aweb_{MARK}-DEADBEEFCAFEBABE0123456789"
SECRETS = (K1, K2)
PREFIXES = (4, 6, 8, 12, 16)

#: The ONE shape the API starts on: API_KEYS a JSON list, the side maps JSON dicts.
REAL = {
    "API_KEYS": json.dumps([K1, K2]),
    "API_KEY_TENANTS": json.dumps({K1: "asm-ro", K2: "svc-asm-web"}),
    "API_KEY_ROLES": json.dumps({K1: "user", K2: "admin"}),
}


def _assert_clean(blob: str, where: str) -> None:
    for s in SECRETS:
        assert s not in blob, f"{where}: full secret leaked"
        for n in PREFIXES:
            assert s[:n] not in blob, f"{where}: {n}-char prefix leaked"
    assert MARK not in blob, f"{where}: marker leaked"


# --- the structural property, on the RETURN VALUE -------------------------------

def test_no_key_value_or_fragment_reaches_a_summary() -> None:
    infos = summarize(REAL)
    _assert_clean(repr(infos) + json.dumps([i.__dict__ for i in infos], default=str), "summary")
    assert [i.subject for i in infos] == ["asm-ro", "svc-asm-web"]
    assert [i.role for i in infos] == ["user", "admin"]


def test_fingerprint_is_a_sha256_prefix_not_a_key_head() -> None:
    """The review's mutation: `fingerprint` returning `value[:6]` passed the old
    canary, whose shortest refused prefix was 8. Pin what the field IS."""
    import hashlib
    i = summarize(REAL)[0]
    assert i.fingerprint == hashlib.sha256(K1.encode()).hexdigest()[:12]
    assert not K1.startswith(i.fingerprint)


def test_apikeyinfo_has_no_length_and_secret_has_no_len() -> None:
    i = ApiKeyInfo(fingerprint="abc", subject="s", role="user")
    assert not hasattr(i, "length") and not hasattr(i, "prefix")
    with pytest.raises(TypeError):
        len(Secret(K1))  # type: ignore[arg-type]


# --- the structural property, on the CLI's ACTUAL OUTPUT -------------------------
#
# Each tenant here is a shape the review used to reproduce a leak, plus the
# real one. The assertion is on what the tool PRINTS, which is what a human
# copies into a transcript.

def _tenant(root, name: str, tenant_env: str = "", secrets_env: str = "") -> None:
    cfg = root / name / "config"
    cfg.mkdir(parents=True)
    (cfg / "tenant.env").write_text(tenant_env or "QDRANT_URL=http://x\n")
    if secrets_env:
        (cfg / "secrets.env").write_text(secrets_env)


def _q(v: str) -> str:
    """Single-quote a value the way the tenant files do (they are sourced)."""
    return "'" + v + "'"


LEAK_SHAPES = {
    # the review's t22 / c2: inverted maps — values ARE secrets
    "inverted-tenants": f"API_KEYS={_q(json.dumps([K1, K2]))}\nAPI_KEY_TENANTS={_q(json.dumps({'asm-ro': K1, 'svc': K2}))}\n",
    "map-values-are-secrets": f"API_KEYS={_q(json.dumps([K1, K2]))}\nAPI_KEY_TENANTS={_q(json.dumps({K1: K2, K2: K1}))}\nAPI_KEY_ROLES={_q(json.dumps({K1: K2}))}\n",
    # t11: a list where a dict belongs
    "tenants-is-a-list": f"API_KEYS={_q(json.dumps([K1]))}\nAPI_KEY_TENANTS={_q(json.dumps([K1, K2]))}\n",
    # t06: editor-wrapped dict — parse_env_file cannot balance the quote and keeps the raw first line
    "pretty-printed-dict": f"API_KEYS={_q(json.dumps([K1]))}\nAPI_KEY_TENANTS='{{\"{K1}\": \"asm-ro\",\n  \"{K2}\": \"svc\"}}'\n",
    # t08/t09: shell metacharacters around the value
    "shell-substitution": f"API_KEYS={_q(json.dumps([K1]))}\nAPI_KEY_TENANTS=$({K2})\n",
    "backticks": f"API_KEYS={_q(json.dumps([K1]))}\nAPI_KEY_TENANTS=`{K2}`\n",
    # the shapes the API refuses to start on — must raise, never parse
    "comma-list-keys": f"API_KEYS={_q(K1 + ',' + K2)}\nAPI_KEY_TENANTS={_q('asm-ro,svc')}\n",
    "dict-keys": f"API_KEYS={_q(json.dumps({K1: 'asm-ro', K2: 'svc'}))}\n",
    "bare-string-keys": f"API_KEYS={_q(K1)}\n",
    "json-string-keys": f"API_KEYS={_q(json.dumps(K1))}\n",
    # a value with a newline inside the JSON
    "newline-in-value": f"API_KEYS={_q(json.dumps([K1 + chr(10) + K2]))}\n",
    # an unmapped key
    "unmapped-key": f"API_KEYS={_q(json.dumps([K1, K2]))}\nAPI_KEY_TENANTS={_q(json.dumps({K1: 'asm-ro'}))}\n",
    # a label that is a fragment of a key
    "label-is-a-key-fragment": f"API_KEYS={_q(json.dumps([K1]))}\nAPI_KEY_TENANTS={_q(json.dumps({K1: K1[:20]}))}\n",
    # a role that is not one the API knows
    "unknown-role": f"API_KEYS={_q(json.dumps([K1]))}\nAPI_KEY_ROLES={_q(json.dumps({K1: 'superuser-' + MARK}))}\n",
}


@pytest.mark.parametrize("name", sorted(LEAK_SHAPES), ids=sorted(LEAK_SHAPES))
def test_cli_output_never_carries_a_secret_for_any_shape(tmp_path, capsys, name) -> None:
    """THE canary. Stdout AND stderr of the real CLI, over the shapes that leaked."""
    _tenant(tmp_path, "t", LEAK_SHAPES[name])
    _main(["--root", str(tmp_path)])
    out = capsys.readouterr()
    _assert_clean(out.out + out.err, f"cli[{name}]")


def test_cli_output_is_clean_for_the_real_shape_too(tmp_path, capsys) -> None:
    _tenant(tmp_path, "t", "\n".join(f"{k}={_q(v)}" for k, v in REAL.items()) + "\n")
    _main(["--root", str(tmp_path), "--by-subject"])
    out = capsys.readouterr().out
    _assert_clean(out, "cli[real,--by-subject]")
    assert "asm-ro" in out and "svc-asm-web" in out and "2 keys" in out


# --- what those shapes DO, individually ------------------------------------------

def test_shapes_the_api_refuses_raise_rather_than_parse() -> None:
    """A parser that accepts what config.py rejects describes a deployment that
    cannot exist — and those branches were where the leaks lived."""
    for bad in (K1 + "," + K2, json.dumps({K1: "asm-ro"}), K1, json.dumps(K1), "[unterminated",
                json.dumps([K1, 7]), json.dumps([[K1]])):
        with pytest.raises(UnrecognisedKeyConfig):
            summarize({"API_KEYS": bad})
    for bad in (json.dumps([K1, K2]), "asm-ro,svc", json.dumps({K1: 3}), json.dumps("x")):
        with pytest.raises(UnrecognisedKeyConfig):
            summarize({"API_KEYS": json.dumps([K1]), "API_KEY_TENANTS": bad})


def test_the_exception_message_carries_no_key_material() -> None:
    for env in ({"API_KEYS": "[" + json.dumps(K1) + ",,]"},
                {"API_KEYS": K1},
                {"API_KEYS": json.dumps([K1]), "API_KEY_TENANTS": json.dumps([K2])}):
        with pytest.raises(UnrecognisedKeyConfig) as ei:
            summarize(env)
        _assert_clean(str(ei.value), "exception")


def test_an_unmapped_key_says_so_rather_than_falling_back() -> None:
    infos = summarize({"API_KEYS": json.dumps([K1, K2]), "API_KEY_TENANTS": json.dumps({K1: "asm-ro"})})
    assert [i.subject for i in infos] == ["asm-ro", UNMAPPED]


def test_a_map_value_that_is_not_a_label_is_unrecognised_not_printed() -> None:
    inverted = summarize({"API_KEYS": json.dumps([K1, K2]), "API_KEY_TENANTS": json.dumps({K1: K2, K2: K1})})
    assert [i.subject for i in inverted] == [UNRECOGNISED, UNRECOGNISED]
    fragment = summarize({"API_KEYS": json.dumps([K1]), "API_KEY_TENANTS": json.dumps({K1: K1[:20]})})
    assert fragment[0].subject == UNRECOGNISED
    shellish = summarize({"API_KEYS": json.dumps([K1]), "API_KEY_TENANTS": json.dumps({K1: "$(cat /etc/passwd)"})})
    assert shellish[0].subject == UNRECOGNISED


def test_roles_are_allowlisted_against_the_apis_own_constants() -> None:
    from ragstack.api.security import ROLE_ADMIN, ROLE_RESEARCHER, ROLE_USER
    assert KNOWN_ROLES == {ROLE_ADMIN, ROLE_USER, ROLE_RESEARCHER}
    infos = summarize({"API_KEYS": json.dumps([K1, K2]), "API_KEY_ROLES": json.dumps({K1: "admin", K2: K1})})
    assert [i.role for i in infos] == ["admin", UNRECOGNISED]
    assert summarize({"API_KEYS": json.dumps([K1])})[0].role == ""


def test_reserved_prefix_collisions_counts_without_naming_and_raises_on_junk() -> None:
    assert reserved_prefix_collisions(REAL) == 0
    assert reserved_prefix_collisions({"API_KEYS": json.dumps(["rsk_x", K1])}) == 1
    with pytest.raises(UnrecognisedKeyConfig):  # was -1, which sums and compares like a number
        reserved_prefix_collisions({"API_KEYS": "[unterminated"})


def test_absent_config_is_empty_not_an_error() -> None:
    assert summarize({}) == []
    assert summarize({"API_KEYS": ""}) == []


# --- Secret -----------------------------------------------------------------------

@pytest.mark.parametrize("render", [repr, str, "{}".format, lambda s: f"{s}", lambda s: f"{s!r}", lambda s: f"{s}"])
def test_a_secret_never_renders_itself(render) -> None:
    assert K1 not in render(Secret(K1)) and "redacted" in render(Secret(K1))


def test_a_secret_survives_json_default_str_and_refuses_to_pickle() -> None:
    assert K1 not in json.dumps({"key": Secret(K1)}, default=str)
    with pytest.raises(TypeError):
        pickle.dumps(Secret(K1))


def test_reveal_is_the_only_way_out() -> None:
    assert Secret(K1).reveal() == K1


# --- the files ---------------------------------------------------------------------

def test_a_tenant_whose_keys_live_in_secrets_env_is_not_reported_as_zero(tmp_path) -> None:
    """The false negative that actually happened: hackathon keeps its keys in
    `secrets.env`; a reader of `tenant.env` alone reported 0 keys for a tenant
    holding thirty. Under-reporting is the dangerous direction on an inventory."""
    from ragstack.ops.tenant_keys import summarize_tenant
    _tenant(tmp_path, "t1", secrets_env=f"API_KEYS={_q(json.dumps([K1]))}\nAPI_KEY_TENANTS={_q(json.dumps({K1: 'a-01'}))}\n")
    infos = summarize_tenant("t1", root=str(tmp_path))
    assert [i.subject for i in infos] == ["a-01"]
    _assert_clean(repr(infos), "secrets.env tenant")


def test_a_non_utf8_tenant_file_does_not_abort_the_listing(tmp_path, capsys) -> None:
    """The review: one bad byte raised through `_main` and every tenant after it
    went unreported. Now it is one line and the listing continues."""
    _tenant(tmp_path, "a", "\n".join(f"{k}={_q(v)}" for k, v in REAL.items()) + "\n")
    cfg = tmp_path / "bad" / "config"
    cfg.mkdir(parents=True)
    (cfg / "tenant.env").write_bytes(b"API_KEYS=\xff\xfe\n")
    _tenant(tmp_path, "z", "\n".join(f"{k}={_q(v)}" for k, v in REAL.items()) + "\n")
    _main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "bad: unreadable (UnicodeDecodeError)" in out
    assert out.count("2 keys") == 2  # both a and z reported
    _assert_clean(out, "listing with a bad tenant")
