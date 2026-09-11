"""Replay of ``contracts/fixtures/identity/bvbrc/vectors.json`` against the
Python BV-BRC verifier.

The generated-key tests in ``test_identity_bvbrc.py`` prove the verifier; this
file proves the *fixture* — the same vectors the Go control plane's verifier
(ADR-0007) replays — so the two implementations are held to one table. Nothing
here is signed at test time: every token is read from the committed file, the
key server is a fake serving the committed ``public_key.json``, and the clock is
pinned to the file's ``now``. No network, nothing read from ``~``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization

from ragstack.identity import BvbrcSignedToken, IdentityInvalid, IdentityUnavailable
from ragstack.identity import bvbrc as bvbrc_mod
from tests.identity_support import FakeKeyServer

FIXTURES = Path(__file__).resolve().parents[3] / "contracts" / "fixtures" / "identity" / "bvbrc"
VECTORS = json.loads((FIXTURES / "vectors.json").read_text(encoding="utf-8"))
PUBLIC_KEY = json.loads((FIXTURES / "public_key.json").read_text(encoding="utf-8"))

_ERROR_KINDS = {"invalid": IdentityInvalid, "unavailable": IdentityUnavailable}


def _server(body: object = PUBLIC_KEY, allowlist: list[str] | None = None) -> FakeKeyServer:
    """Serve ``body`` at every allowlisted URL — a vector may name any of them."""
    urls = allowlist if allowlist is not None else VECTORS["allowlist"]
    return FakeKeyServer(routes=dict.fromkeys(urls, body))


def _provider(server: FakeKeyServer, allowlist: list[str]) -> BvbrcSignedToken:
    return BvbrcSignedToken(
        allowlist=allowlist, http_client=server.client(), min_refetch_interval=0.0
    )


# --------------------------------------------------------------------------- #
# The fixture triple is self-consistent
# --------------------------------------------------------------------------- #


def test_fixture_schema_version_and_shape():
    assert VECTORS["schema_version"] == 1
    assert VECTORS["signing_subject_url"] in VECTORS["allowlist"]
    assert tuple(VECTORS["allowlist"]) == bvbrc_mod.DEFAULT_SIGNING_SUBJECTS
    names = [v["name"] for v in VECTORS["vectors"]]
    assert len(names) == len(set(names))
    for v in VECTORS["vectors"]:
        assert set(v) <= {"name", "token", "allowlist", "now", "expect"}, v["name"]
        expect = v["expect"]
        if expect["ok"]:
            assert {"subject", "token_id", "expires_at"} <= set(expect), v["name"]
        else:
            assert expect["error_kind"] in _ERROR_KINDS, v["name"]


def test_public_key_json_is_the_test_keys_public_half():
    """public_key.json must be what the key server would say about
    test-signing-key.pem — otherwise every 'valid' vector is testing nothing."""
    private = serialization.load_pem_private_key(
        (FIXTURES / "test-signing-key.pem").read_bytes(), password=None
    )
    expected = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    assert PUBLIC_KEY == {"pubkey": expected}
    assert private.key_size == 1024  # BV-BRC's size; the README says test-only


def test_pinned_clock_keeps_the_file_from_rotting():
    """`now` sits between every expired and every valid expiry, and the valid
    ones are far enough out that the file stays valid for decades."""
    now = VECTORS["now"]
    assert now < time.time(), "now must be in the past relative to real time"
    for v in VECTORS["vectors"]:
        if v["expect"]["ok"] and "now" not in v:
            assert v["expect"]["expires_at"] > now, v["name"]
    valid = next(v for v in VECTORS["vectors"] if v["name"] == "valid")
    assert valid["expect"]["expires_at"] >= 4102444800  # 2100-01-01


# --------------------------------------------------------------------------- #
# The replay
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("vector", VECTORS["vectors"], ids=[v["name"] for v in VECTORS["vectors"]])
async def test_vector(vector: dict, monkeypatch):
    allowlist = vector.get("allowlist", VECTORS["allowlist"])
    now = vector.get("now", VECTORS["now"])
    monkeypatch.setattr(bvbrc_mod.time, "time", lambda: float(now))

    server = _server()
    provider = _provider(server, allowlist)
    expect = vector["expect"]

    if expect["ok"]:
        identity = await provider.authenticate(vector["token"])
        assert identity.issuer == "bvbrc"
        assert identity.subject == expect["subject"]
        assert identity.token_id == expect["token_id"]
        assert identity.expires_at == expect["expires_at"]
        return

    with pytest.raises(_ERROR_KINDS[expect["error_kind"]]) as excinfo:
        await provider.authenticate(vector["token"])
    substring = expect.get("reason_substring")
    if substring:
        assert substring in str(excinfo.value), (vector["name"], str(excinfo.value))


@pytest.mark.parametrize(
    "name",
    ["signing_subject_not_in_allowlist", "missing_signing_subject", "expired", "no_expiry"],
)
async def test_rejected_before_any_key_fetch(name: str, monkeypatch):
    """The pin and the expiry check run BEFORE the network: a forged subject or
    a stale token must never cause a key-server round trip."""
    vector = next(v for v in VECTORS["vectors"] if v["name"] == name)
    monkeypatch.setattr(bvbrc_mod.time, "time", lambda: float(VECTORS["now"]))
    server = _server()
    with pytest.raises(IdentityInvalid):
        await _provider(server, VECTORS["allowlist"]).authenticate(vector["token"])
    assert server.total_hits() == 0


@pytest.mark.parametrize(
    "variant",
    VECTORS["key_server_variants"],
    ids=[v["name"] for v in VECTORS["key_server_variants"]],
)
async def test_key_server_variant(variant: dict, monkeypatch):
    """Each raw key-server body either yields a key that verifies the valid
    token, or is refused as *unavailable* (never *invalid* — a broken key server
    is our outage, not the caller's fault)."""
    monkeypatch.setattr(bvbrc_mod.time, "time", lambda: float(VECTORS["now"]))
    valid = next(v for v in VECTORS["vectors"] if v["name"] == "valid")
    server = _server(body=variant["body"])
    provider = _provider(server, VECTORS["allowlist"])
    if variant["expect_parse_ok"]:
        identity = await provider.authenticate(valid["token"])
        assert identity.subject == valid["expect"]["subject"]
    else:
        with pytest.raises(IdentityUnavailable):
            await provider.authenticate(valid["token"])


# --------------------------------------------------------------------------- #
# The generator is reproducible
# --------------------------------------------------------------------------- #


def _generator():
    """``python/scripts/gen_bvbrc_vectors.py``, imported by path (``scripts/``
    is not a package)."""
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "scripts" / "gen_bvbrc_vectors.py"
    spec = importlib.util.spec_from_file_location("gen_bvbrc_vectors", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_regenerating_the_fixture_is_byte_identical(tmp_path):
    """Two runs of the generator over the committed keys must produce the
    committed bytes, exactly.

    Both keys are committed for this reason: ``signed_by_another_key`` used to
    be signed with a key generated per run, so every regeneration rewrote that
    one token — a diff that says nothing, in a file the Python and Go verifiers
    are both held to, and which therefore has to be reviewable.
    """
    gen = _generator()
    out = tmp_path / "bvbrc"
    out.mkdir()
    for pem in ("test-signing-key.pem", "test-wrong-key.pem"):
        assert (FIXTURES / pem).is_file(), f"{pem} must be committed, not generated per run"
        (out / pem).write_bytes((FIXTURES / pem).read_bytes())

    assert gen.main(["--out", str(out)]) == 0
    first = {f.name: f.read_bytes() for f in sorted(out.iterdir())}
    assert gen.main(["--out", str(out)]) == 0
    second = {f.name: f.read_bytes() for f in sorted(out.iterdir())}

    assert first == second, "two runs of the generator disagree"
    for name in ("vectors.json", "public_key.json"):
        assert first[name] == (FIXTURES / name).read_bytes(), (
            f"{name} is not what the generator produces from the committed keys — "
            "re-run python/scripts/gen_bvbrc_vectors.py and commit the result"
        )
    # The keys themselves were reused, not silently replaced.
    for pem in ("test-signing-key.pem", "test-wrong-key.pem"):
        assert first[pem] == (FIXTURES / pem).read_bytes()


def test_wrong_key_is_not_the_signing_key():
    """It is only a useful fixture if it really is a different key."""
    signing = (FIXTURES / "test-signing-key.pem").read_bytes()
    wrong = (FIXTURES / "test-wrong-key.pem").read_bytes()
    assert signing != wrong
    wrong_key = serialization.load_pem_private_key(wrong, password=None)
    assert wrong_key.key_size == 1024
    vector = next(v for v in VECTORS["vectors"] if v["name"] == "signed_by_another_key")
    assert vector["expect"]["ok"] is False
