"""`ops.tenant_keys` — describe API keys without ever emitting one.

Three reviews of three earlier versions each got a key onto stdout through the
`subject` column, past a progressively narrower allowlist. This version prints
no config text at all: subjects are hashes, roles are one of three constants,
everything else is a count. So the canary is not "did the allowlist hold" but
"is anything on stdout/stderr derived from a config value other than as a
sha256 prefix" — and the shapes here are every one that beat the allowlists,
with DISTINCT markers per key, because a shared marker once let one key's
window mask another key's leak.
"""
from __future__ import annotations

import hashlib
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
    fingerprint,
    reserved_prefix_collisions,
    resolve_subjects,
    subject_groups,
    summarize,
    summarize_tenant,
)

M1, M2 = "ZQMARKERQZ", "XJMARKERJX"   # distinct per key — never shared; K3 is bare hex, covered by prefix checks
K1 = f"rk-{M1}-SUPERSECRETVALUE123456"
K2 = f"aweb_{M2}-DEADBEEFCAFEBABE0123456789"
K3 = _secrets.token_hex(32)                              # what the ctl mints
SECRETS = (K1, K2, K3)
PREFIXES = (4, 6, 8, 12, 16, 31)

REAL = {
    "API_KEYS": json.dumps([K1, K2]),
    "API_KEY_TENANTS": json.dumps({K1: "asm-ro", K2: "svc-asm-web"}),
    "API_KEY_ROLES": json.dumps({K1: "user", K2: "admin"}),
}


def _assert_clean(blob: str, where: str) -> None:
    for s in SECRETS:
        for n in PREFIXES:
            assert s[:n] not in blob, f"{where}: {n}-char prefix of a key on output"
        assert s not in blob and s[::-1] not in blob and s.upper() not in blob
    for m in (M1, M2):
        assert m not in blob, f"{where}: marker on output"
    assert "asm-ro" not in blob and "svc-asm-web" not in blob, f"{where}: a config LABEL on output"


def _q(v: str) -> str:
    return "'" + v + "'"


def _tenant(root, name: str, tenant_env: str = "", secrets_env: str = "") -> None:
    cfg = root / name / "config"
    cfg.mkdir(parents=True)
    (cfg / "tenant.env").write_text(tenant_env or "QDRANT_URL=http://x\n")
    if secrets_env:
        (cfg / "secrets.env").write_text(secrets_env)


# --- the structural property ---------------------------------------------------------

def test_every_field_is_a_hash_a_marker_or_a_role() -> None:
    infos = summarize(REAL)
    blob = repr(infos) + json.dumps([i.__dict__ for i in infos], default=str)
    _assert_clean(blob, "summary")
    assert [i.fingerprint for i in infos] == [fingerprint(K1), fingerprint(K2)]
    assert [i.subject for i in infos] == [fingerprint("asm-ro"), fingerprint("svc-asm-web")]
    assert [i.role for i in infos] == ["user", "admin"]
    assert set(ApiKeyInfo.__dataclass_fields__) == {"fingerprint", "subject", "role"}


def test_fingerprint_is_a_sha256_prefix_not_a_head() -> None:
    assert fingerprint(K1) == hashlib.sha256(K1.encode()).hexdigest()[:12]
    assert not K1.startswith(fingerprint(K1))
    assert Secret(K1).fingerprint() == fingerprint(K1)


# Every shape that beat an allowlist in reviews 1-3, plus the real one.
SHAPES = {
    "real": "\n".join(f"{k}={_q(v)}" for k, v in REAL.items()) + "\n",
    "inverted-map": f"API_KEYS={_q(json.dumps([K1, K2]))}\nAPI_KEY_TENANTS={_q(json.dumps({'asm-ro': K1, 'svc': K2}))}\n",
    "map-values-are-keys": f"API_KEYS={_q(json.dumps([K1, K2]))}\nAPI_KEY_TENANTS={_q(json.dumps({K1: K2, K2: K1}))}\nAPI_KEY_ROLES={_q(json.dumps({K1: K2}))}\n",
    "other-tenants-key-as-value": f"API_KEYS={_q(json.dumps([K1]))}\nAPI_KEY_TENANTS={_q(json.dumps({K1: K3}))}\n",
    "seven-char-head": f"API_KEYS={_q(json.dumps([K1, K2]))}\nAPI_KEY_TENANTS={_q(json.dumps({K1: K1[:7], K2: K2[:4]}))}\n",
    "overlap-decorated": f"API_KEYS={_q(json.dumps([K1]))}\nAPI_KEY_TENANTS={_q(json.dumps({K1: 'svc-' + K1[3:30]}))}\n",
    "dashed-hex": f"API_KEYS={_q(json.dumps([K3]))}\nAPI_KEY_TENANTS={_q(json.dumps({K3: '-'.join(K3[i:i+7] for i in range(0, 64, 7))}))}\n",
    "upper-hex": f"API_KEYS={_q(json.dumps([K3]))}\nAPI_KEY_TENANTS={_q(json.dumps({K3: K3.upper()[:31] + '-x'}))}\n",
    "reversed": f"API_KEYS={_q(json.dumps([K3]))}\nAPI_KEY_TENANTS={_q(json.dumps({K3: K3[::-1]}))}\n",
    "two-api-keys-lines": f"API_KEYS={_q(json.dumps([K1]))}\nAPI_KEYS={_q(json.dumps([K2]))}\nAPI_KEY_TENANTS={_q(json.dumps({K2: K1}))}\n",
    "pretty-printed-dict": f"API_KEYS={_q(json.dumps([K1]))}\nAPI_KEY_TENANTS='{{\"{K1}\": \"asm-ro\",\n  \"{K2}\": \"svc\"}}'\n",
    "shell-substitution": f"API_KEYS={_q(json.dumps([K1]))}\nAPI_KEY_TENANTS=$({K2})\n",
    "tenants-is-a-list": f"API_KEYS={_q(json.dumps([K1]))}\nAPI_KEY_TENANTS={_q(json.dumps([K1, K2]))}\n",
    "comma-list-keys": f"API_KEYS={_q(K1 + ',' + K2)}\nAPI_KEY_TENANTS={_q('asm-ro,svc')}\n",
    "dict-keys": f"API_KEYS={_q(json.dumps({K1: 'asm-ro', K2: 'svc'}))}\n",
    "bare-string-keys": f"API_KEYS={_q(K1)}\n",
    "unknown-role": f"API_KEYS={_q(json.dumps([K1]))}\nAPI_KEY_ROLES={_q(json.dumps({K1: 'superuser-' + M1}))}\n",
    "role-is-a-key": f"API_KEYS={_q(json.dumps([K1, K2]))}\nAPI_KEY_ROLES={_q(json.dumps({K1: K2}))}\n",
}


@pytest.mark.parametrize("name", sorted(SHAPES), ids=sorted(SHAPES))
def test_cli_output_carries_nothing_from_the_config_for_any_shape(tmp_path, capsys, name) -> None:
    """THE canary: the CLI's real stdout+stderr, over every shape that beat an
    allowlist, with the tenant's owner NOT in the run for the cross-tenant case."""
    _tenant(tmp_path, "t", SHAPES[name])
    _main(["--root", str(tmp_path)])
    out = capsys.readouterr()
    _assert_clean(out.out + out.err, f"cli[{name}]")


def test_rotation_residue_and_cross_tenant_print_only_hashes(tmp_path, capsys) -> None:
    """Round-2/3 cases: a key not in the tenant's FINAL list, sitting in a map
    value. No harvest needed now — the value is hashed like every other label."""
    _tenant(tmp_path, "a", f"API_KEYS={_q(json.dumps([K1]))}\n")
    _tenant(tmp_path, "b", f"API_KEYS={_q(json.dumps([K3]))}\nAPI_KEY_TENANTS={_q(json.dumps({K3: K1}))}\n")
    _tenant(tmp_path, "r",
            tenant_env=f"API_KEYS={_q(json.dumps([K2]))}\n",
            secrets_env=f"API_KEYS={_q(json.dumps([K3]))}\nAPI_KEY_TENANTS={_q(json.dumps({K3: K2}))}\n")
    _main(["--root", str(tmp_path), "b", "r"])          # owner `a` NOT in the run
    _assert_clean(capsys.readouterr().out, "cross-tenant / residue")
    assert summarize_tenant("b", root=str(tmp_path))[0].subject == fingerprint(K1)


# --- usefulness: the audit works on hashes -------------------------------------------

def test_subject_groups_give_the_thirty_keys_one_subject_audit_without_names(tmp_path, capsys) -> None:
    keys = [_secrets.token_hex(32) for _ in range(31)]
    tenants = dict.fromkeys(keys[:30], "hackathon-ro") | {keys[30]: "svc-hackathon-admin"}
    _tenant(tmp_path, "hack", f"API_KEYS={_q(json.dumps(keys))}\nAPI_KEY_TENANTS={_q(json.dumps(tenants))}\n"
                              f"API_KEY_ROLES={_q(json.dumps({keys[30]: 'admin'}))}\n")
    infos = summarize_tenant("hack", root=str(tmp_path))
    assert subject_groups(infos) == {fingerprint("hackathon-ro"): 30, fingerprint("svc-hackathon-admin"): 1}
    _main(["--root", str(tmp_path), "--subject", "hackathon-ro", "--subject", "nobody"])
    out = capsys.readouterr().out
    assert "hack: 31 keys" in out and "subjects=2 distinct" in out
    assert f" 30  subject={fingerprint('hackathon-ro')}" in out
    assert "--subject 'hackathon-ro': 30 keys" in out and "--subject 'nobody': 0 keys" in out
    for k in keys:
        assert k[:8] not in out
    assert "hackathon-ro" in out          # the operator's OWN input echoes back...
    assert "svc-hackathon-admin" not in out  # ...a label from the config never does


def test_subject_candidate_that_differs_by_case_or_space_reports_zero_and_echoes_nothing(tmp_path, capsys) -> None:
    """A mutant matching case-insensitively and echoing the CONFIG's spelling passed
    every earlier test: the only --subject tests used exact labels."""
    _tenant(tmp_path, "t", SHAPES["real"])
    # candidates that differ by case/underscore only — none CONTAINS the label,
    # so the operator echo cannot mask a config echo
    _main(["--root", str(tmp_path), "--subject", "ASM-RO", "--subject", "asm_ro", "--subject", "Svc-Asm-Web"])
    out = capsys.readouterr().out
    assert "--subject 'ASM-RO': 0 keys" in out and "--subject 'asm_ro': 0 keys" in out
    assert "--subject 'Svc-Asm-Web': 0 keys" in out
    _assert_clean(out, "case-variant --subject")


def test_resolve_subjects_matches_by_hash() -> None:
    infos = summarize(REAL)
    assert resolve_subjects(infos, ["asm-ro", "svc-asm-web", "other"]) == {"asm-ro": 1, "svc-asm-web": 1, "other": 0}


def test_an_unmapped_key_says_so() -> None:
    infos = summarize({"API_KEYS": json.dumps([K1, K2]), "API_KEY_TENANTS": json.dumps({K1: "asm-ro"})})
    assert [i.subject for i in infos] == [fingerprint("asm-ro"), UNMAPPED]


def test_roles_are_a_closed_set_equal_to_the_apis_constants() -> None:
    from ragstack.api.security import ROLE_ADMIN, ROLE_RESEARCHER, ROLE_USER
    assert KNOWN_ROLES == {ROLE_ADMIN, ROLE_USER, ROLE_RESEARCHER}
    infos = summarize({"API_KEYS": json.dumps([K1, K2]), "API_KEY_ROLES": json.dumps({K1: "admin", K2: K1})})
    assert [i.role for i in infos] == ["admin", UNRECOGNISED]
    assert summarize({"API_KEYS": json.dumps([K1])})[0].role == ""


# --- rule 2: the API's shapes, nothing else --------------------------------------------

def test_shapes_the_api_refuses_raise_rather_than_parse() -> None:
    for bad in (K1 + "," + K2, json.dumps({K1: "asm-ro"}), K1, json.dumps(K1), "[unterminated",
                json.dumps([K1, 7]), json.dumps([[K1]]), "[" * 100000 + "]" * 100000):
        with pytest.raises(UnrecognisedKeyConfig):
            summarize({"API_KEYS": bad})
    for bad in (json.dumps([K1, K2]), "asm-ro,svc", json.dumps({K1: 3}), json.dumps("x")):
        with pytest.raises(UnrecognisedKeyConfig):
            summarize({"API_KEYS": json.dumps([K1]), "API_KEY_TENANTS": bad})


def test_the_exception_message_carries_no_key_material() -> None:
    for env in ({"API_KEYS": "[" + json.dumps(K1) + ",,]"}, {"API_KEYS": K1},
                {"API_KEYS": json.dumps([K1]), "API_KEY_TENANTS": json.dumps([K2])}):
        with pytest.raises(UnrecognisedKeyConfig) as ei:
            summarize(env)
        _assert_clean(str(ei.value), "exception")


def test_absent_config_is_empty_but_present_and_empty_is_a_boot_failure() -> None:
    assert summarize({}) == []
    for env in ({"API_KEYS": ""}, {"API_KEYS": "  "}, {"API_KEYS": json.dumps([K1]), "API_KEY_TENANTS": ""}):
        with pytest.raises(UnrecognisedKeyConfig, match="present but empty"):
            summarize(env)


def test_a_lone_surrogate_in_a_label_or_key_yields_a_hash_not_a_hidden_tenant() -> None:
    """json.loads accepts it and the API starts on it; a plain .encode() would
    raise and make one odd label hide every key in the tenant."""
    env = {"API_KEYS": json.dumps([K1, "\ud800" + K2]),
           "API_KEY_TENANTS": json.dumps({K1: "asm\ud800ro"})}
    infos = summarize(env)
    assert len(infos) == 2 and all(len(i.fingerprint) == 12 for i in infos)
    assert infos[0].subject == fingerprint("asm\ud800ro")


def test_summarize_holds_only_secrets_so_a_locals_traceback_shows_no_key() -> None:
    import traceback
    from unittest import mock
    with mock.patch("ragstack.ops.tenant_keys.ApiKeyInfo", side_effect=RuntimeError("boom")):
        try:
            summarize(REAL)
        except RuntimeError:
            tb = "".join(traceback.TracebackException(*__import__("sys").exc_info(), capture_locals=True).format())
    frame = tb[tb.index("in summarize"):]          # the frame that holds keys/subjects/roles
    _assert_clean(frame, "summarize locals")


def test_reserved_prefix_collisions_counts_without_naming_and_raises_on_junk() -> None:
    assert reserved_prefix_collisions(REAL) == 0
    assert reserved_prefix_collisions({"API_KEYS": json.dumps(["rsk_x", K1])}) == 1
    with pytest.raises(UnrecognisedKeyConfig):
        reserved_prefix_collisions({"API_KEYS": "[unterminated"})


# --- Secret --------------------------------------------------------------------------

@pytest.mark.parametrize("render", [repr, str, "{}".format, lambda s: f"{s}", lambda s: f"{s!r}"])
def test_a_secret_never_renders_itself(render) -> None:
    assert K1 not in render(Secret(K1)) and "redacted" in render(Secret(K1))


def test_a_secret_survives_json_default_str_refuses_pickle_and_has_no_len() -> None:
    assert K1 not in json.dumps({"key": Secret(K1)}, default=str)
    with pytest.raises(TypeError):
        pickle.dumps(Secret(K1))
    with pytest.raises(TypeError):
        len(Secret(K1))  # type: ignore[arg-type]
    assert Secret(K1).reveal() == K1


# --- files and robustness ------------------------------------------------------------

def test_keys_in_secrets_env_are_not_reported_as_zero_and_later_file_wins(tmp_path) -> None:
    _tenant(tmp_path, "t1", secrets_env=f"API_KEYS={_q(json.dumps([K1]))}\nAPI_KEY_TENANTS={_q(json.dumps({K1: 'a-01'}))}\n")
    assert [i.subject for i in summarize_tenant("t1", root=str(tmp_path))] == [fingerprint("a-01")]
    _tenant(tmp_path, "t2", tenant_env=f"API_KEYS={_q(json.dumps([K1]))}\n", secrets_env=f"API_KEYS={_q(json.dumps([K2]))}\n")
    assert [i.fingerprint for i in summarize_tenant("t2", root=str(tmp_path))] == [fingerprint(K2)]


def test_bad_tenants_are_one_line_each_and_the_listing_continues(tmp_path, capsys) -> None:
    _tenant(tmp_path, "a", SHAPES["real"])
    cfg = tmp_path / "bad" / "config"
    cfg.mkdir(parents=True)
    (cfg / "tenant.env").write_bytes(b"API_KEYS=\xff\xfe\n")
    _tenant(tmp_path, "deep", "API_KEYS=" + _q("[" * 100000 + "]" * 100000) + "\n")
    _tenant(tmp_path, "z", SHAPES["real"])
    _main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "bad: unreadable (UnicodeDecodeError)" in out
    assert "deep: UNRECOGNISED key config" in out
    assert out.count("2 keys") == 2
    _assert_clean(out, "listing with bad tenants")
    assert _main(["--root", str(tmp_path / "nope")]) == 1
    assert "no such tenant root" in capsys.readouterr().out
