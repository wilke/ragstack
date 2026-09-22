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
import secrets as _secrets

import pytest

from ragstack.ops.tenant_keys import (
    KNOWN_ROLES,
    UNMAPPED,
    UNRECOGNISED,
    ApiKeyInfo,
    Secret,
    UnrecognisedKeyConfig,
    _main,
    key_material,
    reserved_prefix_collisions,
    summarize,
    summarize_tenant,
)

MARK = "ZQMARKERQZ"
# Shaped like keys on this host: NOT embedding their subject. A key of the form
# `rk-<subject>-<random>` would make its own subject a substring of itself and
# the fragment rule would (correctly, by its contract) suppress it — see the note
# at `_FRAGMENT_LEN` in the module. The ctl mints `token_hex(32)`, so the real
# case is covered by `test_real_subject_forms_on_this_host_are_never_suppressed`.
K1 = f"rk-{MARK}-SUPERSECRETVALUE123456"
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
    # second review — A1: a 7-char key head, below the old threshold, as a label
    "seven-char-key-head": f"API_KEYS={_q(json.dumps([K1, K2]))}\nAPI_KEY_TENANTS={_q(json.dumps({K1: K1[:7], K2: K2[:4]}))}\n",
    # A2/A3: overlaps a key by 20 chars but is not a substring either way
    "overlap-not-containment": f"API_KEYS={_q(json.dumps([K1]))}\nAPI_KEY_TENANTS={_q(json.dumps({K1: K1[:20] + 'x'}))}\n",
    "overlap-decorated-both-ends": f"API_KEYS={_q(json.dumps([K1]))}\nAPI_KEY_TENANTS={_q(json.dumps({K1: 'svc-' + K1[3:30]}))}\n",
    # the whole key inside a longer label
    "label-contains-whole-key": f"API_KEYS={_q(json.dumps([K1]))}\nAPI_KEY_TENANTS={_q(json.dumps({K1: K1 + '-ro'}))}\n",
    # A9g: two API_KEYS lines in one file — parse_env_file keeps the last; the first is still key material
    "two-api-keys-lines": f"API_KEYS={_q(json.dumps([K1]))}\nAPI_KEYS={_q(json.dumps([K2]))}\nAPI_KEY_TENANTS={_q(json.dumps({K2: K1}))}\n",
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

@pytest.mark.parametrize("render", [repr, str, "{}".format, lambda s: f"{s}", lambda s: f"{s!r}"])
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



# --- the second review: keys the tenant's FINAL list does not contain -----------------
#
# A 64-hex key is a syntactically valid label. The first rewrite checked fragments
# only against the tenant's merged API_KEYS, so a key that was not in that list —
# another tenant's, or this tenant's own superseded one — printed in full.


HEXA = _secrets.token_hex(32)   # what `ragstack-ctl key mint` emits
HEXB = _secrets.token_hex(32)


def _hex_clean(blob: str, where: str) -> None:
    for h in (HEXA, HEXB):
        assert h not in blob, f"{where}: hex key leaked"
        for n in (4, 8, 16, 32):
            assert h[:n] not in blob, f"{where}: {n}-char hex prefix leaked"


def test_r1_cross_tenant_pasted_key_is_not_printed(tmp_path, capsys) -> None:
    """Tenant b's map carries tenant a's key as a VALUE (a paste mistake)."""
    _tenant(tmp_path, "a", f"API_KEYS={_q(json.dumps([HEXA]))}\n")
    _tenant(tmp_path, "b", f"API_KEYS={_q(json.dumps([HEXB]))}\nAPI_KEY_TENANTS={_q(json.dumps({HEXB: HEXA}))}\n")
    _main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    _hex_clean(out, "cross-tenant")
    assert "subject=(unrecognised)" in out


def test_r2_rotation_residue_in_tenant_env_is_not_printed(tmp_path, capsys) -> None:
    """tenant.env still lists the OLD key; secrets.env (later, wins) lists the new
    one and maps it to the old key's value. The old key is not in the merged
    list, so only a pre-merge harvest can know it is a key."""
    _tenant(tmp_path, "t",
            tenant_env=f"API_KEYS={_q(json.dumps([HEXA]))}\nAPI_KEY_TENANTS={_q(json.dumps({HEXA: 'asm-ro'}))}\n",
            secrets_env=f"API_KEYS={_q(json.dumps([HEXB]))}\nAPI_KEY_TENANTS={_q(json.dumps({HEXB: HEXA}))}\n")
    _main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    _hex_clean(out, "rotation residue")
    infos = summarize_tenant("t", root=str(tmp_path))
    assert [i.subject for i in infos] == [UNRECOGNISED]
    # later file wins for the merged view: exactly one key, the new one
    import hashlib
    assert [i.fingerprint for i in infos] == [hashlib.sha256(HEXB.encode()).hexdigest()[:12]]


def test_a_key_from_outside_the_run_is_refused_by_shape(tmp_path, capsys) -> None:
    """The case no harvest can cover: the pasted key belongs to a tenant that is
    not being scanned. A hex-only label of 32+ chars, or an rsk_ label, is a
    credential by shape — no subject on this host is hex-only."""
    _tenant(tmp_path, "t", f"API_KEYS={_q(json.dumps([HEXB]))}\nAPI_KEY_TENANTS={_q(json.dumps({HEXB: HEXA}))}\n")
    _main(["--root", str(tmp_path), "t"])
    _hex_clean(capsys.readouterr().out, "outside-run hex")
    assert summarize({"API_KEYS": json.dumps([K1]), "API_KEY_TENANTS": json.dumps({K1: HEXA})})[0].subject == UNRECOGNISED
    assert summarize({"API_KEYS": json.dumps([K1]), "API_KEY_TENANTS": json.dumps({K1: "rsk_" + "x" * 30})})[0].subject == UNRECOGNISED


def test_key_material_is_harvested_per_file_and_from_every_shape(tmp_path) -> None:
    (tmp_path / "f.env").write_text(
        f"API_KEYS={_q(json.dumps([HEXA]))}\nAPI_KEY_TENANTS={_q(json.dumps({HEXB: 'x', 'y': K1}))}\n"
        f"API_KEY_ROLES='{{\"{K2}\": \"admin\",\n  \"broken\"'\n"
    )
    km = key_material([tmp_path / "f.env"])
    assert {HEXA, HEXB, "y"} <= km                        # API_KEYS items and side-map dict KEYS
    assert "x" not in km and K1 not in km                 # never a map's VALUES: those are labels
    assert not any(K2 in raw for raw in km)               # nor a map's raw text (unparseable here)


@pytest.mark.parametrize("label", [
    "asm-ro", "svc-asm-web", "svc-askclark", "svc-lucid-web", "hackathon-a-01", "hackathon-ro",
    "attendee-01", "svc-hackathon-admin", "bvbrc:alice@patricbrc.org", "bvbrc:awilke@bvbrc",
])
def test_real_subject_forms_on_this_host_are_never_suppressed(label) -> None:
    """The allowlist has to stay useful. Every subject form seen in docs, runbooks
    and conformance, against hex keys as the ctl mints them."""
    keys = [_secrets.token_hex(32) for _ in range(10)]
    env = {"API_KEYS": json.dumps(keys), "API_KEY_TENANTS": json.dumps(dict.fromkeys(keys, label))}
    assert {i.subject for i in summarize(env)} == {label}


def test_the_label_regex_is_pinned() -> None:
    """Surviving mutants from the second review: whitespace, shell metacharacters
    and the 64-char bound were pinned by nothing."""
    base = {"API_KEYS": json.dumps([K1])}
    def subj(v): return summarize({**base, "API_KEY_TENANTS": json.dumps({K1: v})})[0].subject
    # "g" is not a hex digit, so "g" * 65 tests the BOUND and not the shape rule.
    for bad in ("asm ro", "asm\tro", "$(id)", "`id`", "{a}", "(a)", "a\"b", "a'b", "g" * 65, "", "-leading"):
        assert subj(bad) == UNRECOGNISED, repr(bad)
    assert subj("g" * 64) == "g" * 64            # exactly the bound; "g" is not a hex digit
    assert subj("a" * 64) == UNRECOGNISED         # same length, hex-only: a credential by shape
    assert subj("hackathon-a-01") == "hackathon-a-01"


def test_fragment_rule_is_windowed_not_containment() -> None:
    base = {"API_KEYS": json.dumps([K1])}
    def subj(v): return summarize({**base, "API_KEY_TENANTS": json.dumps({K1: v})})[0].subject
    assert subj(K1[:20] + "x") == UNRECOGNISED         # overlap, not a substring either way
    assert subj("svc-" + K1[3:30]) == UNRECOGNISED     # decorated both ends
    assert subj(K1 + "-ro") == UNRECOGNISED            # whole key inside a longer label
    assert subj(K1[:7]) == UNRECOGNISED                # short head: substring rule
    assert subj(K1[:4]) == UNRECOGNISED
    assert subj("zz" + K1[12:20] + "zz") == UNRECOGNISED  # an interior 8-char window


def test_pathological_values_and_a_missing_root_do_not_traceback(tmp_path, capsys) -> None:
    _tenant(tmp_path, "a", "\n".join(f"{k}={_q(v)}" for k, v in REAL.items()) + "\n")
    _tenant(tmp_path, "deep", "API_KEYS=" + _q("[" * 100000 + "]" * 100000) + "\n")
    _tenant(tmp_path, "z", "\n".join(f"{k}={_q(v)}" for k, v in REAL.items()) + "\n")
    _main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "deep: unreadable (RecursionError)" in out and out.count("2 keys") == 2
    assert _main(["--root", str(tmp_path / "nope")]) == 1
    assert "no such tenant root" in capsys.readouterr().out


def test_known_keys_catch_a_pasted_key_the_shape_rule_cannot(tmp_path, capsys) -> None:
    """The mechanism the run-wide harvest exists for, isolated from the hex rule.

    The mutation battery found that every cross-tenant test used a HEX key,
    which `_looks_like_a_credential` refuses on its own — so `summarize`
    dropping `known_keys` entirely survived. A key that does not look like a
    credential by shape (this repo's synthetic K1 does not) is only suppressible
    because some other file or tenant listed it in API_KEYS.
    """
    _tenant(tmp_path, "a", f"API_KEYS={_q(json.dumps([K1]))}\n")
    _tenant(tmp_path, "b", f"API_KEYS={_q(json.dumps([HEXB]))}\nAPI_KEY_TENANTS={_q(json.dumps({HEXB: K1}))}\n")
    _main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    _assert_clean(out, "cross-tenant non-hex key")
    assert "b: 1 keys" in out and "subject=(unrecognised)" in out
    # The same through the library: without a known-keys set K1 is a valid label.
    env = {"API_KEYS": json.dumps([HEXB]), "API_KEY_TENANTS": json.dumps({HEXB: K1})}
    assert summarize(env, known_keys={K1})[0].subject == UNRECOGNISED
    # Rotation residue, non-hex: tenant.env still lists K1; secrets.env maps the new key to it.
    _tenant(tmp_path, "r",
            tenant_env=f"API_KEYS={_q(json.dumps([K1]))}\n",
            secrets_env=f"API_KEYS={_q(json.dumps([HEXB]))}\nAPI_KEY_TENANTS={_q(json.dumps({HEXB: K1}))}\n")
    assert [i.subject for i in summarize_tenant("r", root=str(tmp_path))] == [UNRECOGNISED]
