"""Provider selection and the boot-time configuration guards."""
from __future__ import annotations

import pytest

from ragstack.config import settings
from ragstack.identity import (
    DEFAULT_SIGNING_SUBJECTS,
    GOOGLE_ISSUERS,
    BvbrcSignedToken,
    CachingIdentityProvider,
    OidcIdentityProvider,
    accepted_issuers,
    build_identity_provider,
    get_identity_provider,
    reset_identity_provider,
    validate_identity_settings,
)


@pytest.fixture(autouse=True)
def _clean_provider():
    yield
    reset_identity_provider()


def test_default_is_off():
    # The flag defaults to `none` so existing deployments are unchanged.
    assert settings.identity_provider == "none"
    assert build_identity_provider(settings) is None
    assert get_identity_provider() is None


def test_default_allowlist_is_the_canonical_bvbrc_set():
    # Straight from BV-BRC's P3AuthConstants.pm. Drifting from it silently locks
    # out a whole deployment (or, worse, admits one nobody vetted).
    assert settings.identity_issuer_allowlist == list(DEFAULT_SIGNING_SUBJECTS)
    assert set(DEFAULT_SIGNING_SUBJECTS) == {
        "https://user.patricbrc.org/public_key",
        "https://user.bv-brc.org/public_key",
        "https://user.alpha.patricbrc.org/public_key",
        "https://user.beta.patricbrc.org/public_key",
    }


def test_bvbrc_provider_is_built_and_cached(monkeypatch):
    monkeypatch.setattr(settings, "identity_provider", "bvbrc")
    provider = build_identity_provider(settings)
    assert isinstance(provider, CachingIdentityProvider)
    assert isinstance(provider._inner, BvbrcSignedToken)


def test_oidc_provider_is_built(monkeypatch):
    monkeypatch.setattr(settings, "identity_provider", "oidc")
    monkeypatch.setattr(settings, "identity_oidc_issuer", "https://accounts.google.com")
    monkeypatch.setattr(settings, "identity_oidc_client_ids", ["client-1"])
    monkeypatch.setattr(settings, "identity_oidc_issuer_label", "google")
    provider = build_identity_provider(settings)
    assert isinstance(provider, CachingIdentityProvider)
    assert isinstance(provider._inner, OidcIdentityProvider)


def test_provider_is_rebuilt_when_the_flag_changes(monkeypatch):
    assert get_identity_provider() is None
    monkeypatch.setattr(settings, "identity_provider", "bvbrc")
    assert get_identity_provider() is not None


def test_unknown_provider_is_a_build_error(monkeypatch):
    monkeypatch.setattr(settings, "identity_provider", "magic")
    with pytest.raises(RuntimeError, match="magic"):
        build_identity_provider(settings)


# -- accepted `iss` spellings -------------------------------------------------- #


def test_google_issuer_expands_to_both_spellings(monkeypatch):
    monkeypatch.setattr(settings, "identity_oidc_issuer", "https://accounts.google.com")
    assert accepted_issuers(settings) == GOOGLE_ISSUERS
    monkeypatch.setattr(settings, "identity_oidc_issuer", "accounts.google.com")
    assert accepted_issuers(settings) == GOOGLE_ISSUERS


def test_other_issuers_are_taken_literally(monkeypatch):
    monkeypatch.setattr(settings, "identity_oidc_issuer", "https://idp.example.org")
    assert accepted_issuers(settings) == ("https://idp.example.org",)


def test_explicit_allowed_issuers_win(monkeypatch):
    monkeypatch.setattr(settings, "identity_oidc_issuer", "https://accounts.google.com")
    monkeypatch.setattr(settings, "identity_oidc_allowed_issuers", ["https://only.this"])
    assert accepted_issuers(settings) == ("https://only.this",)


# -- boot-time guards ----------------------------------------------------------- #


def test_validation_is_a_no_op_while_the_flag_is_off():
    validate_identity_settings()  # must not raise


def test_unknown_provider_name_fails_at_boot(monkeypatch):
    monkeypatch.setattr(settings, "identity_provider", "bvbrc-v2")
    with pytest.raises(RuntimeError, match="identity_provider"):
        validate_identity_settings()


def test_bvbrc_without_an_allowlist_fails_at_boot(monkeypatch):
    """An unpinned SigningSubject is the forgery vector — refuse to start rather
    than run with the guard disabled."""
    monkeypatch.setattr(settings, "identity_provider", "bvbrc")
    monkeypatch.setattr(settings, "identity_issuer_allowlist", [])
    with pytest.raises(RuntimeError, match="IDENTITY_ISSUER_ALLOWLIST"):
        validate_identity_settings()


def test_oidc_without_client_ids_fails_at_boot(monkeypatch):
    """An unpinned `aud` accepts ID tokens minted for any other application on the
    same IdP. That must be impossible to reach by omission."""
    monkeypatch.setattr(settings, "identity_provider", "oidc")
    monkeypatch.setattr(settings, "identity_oidc_issuer", "https://accounts.google.com")
    monkeypatch.setattr(settings, "identity_oidc_client_ids", [])
    with pytest.raises(RuntimeError, match="IDENTITY_OIDC_CLIENT_IDS"):
        validate_identity_settings()


def test_oidc_without_an_issuer_fails_at_boot(monkeypatch):
    monkeypatch.setattr(settings, "identity_provider", "oidc")
    monkeypatch.setattr(settings, "identity_oidc_issuer", "")
    monkeypatch.setattr(settings, "identity_oidc_client_ids", ["c"])
    with pytest.raises(RuntimeError, match="IDENTITY_OIDC_ISSUER"):
        validate_identity_settings()


def test_an_oversized_cache_ttl_fails_at_boot(monkeypatch):
    # The TTL is the revocation lag, and BV-BRC tokens have no revocation at all.
    monkeypatch.setattr(settings, "identity_provider", "bvbrc")
    monkeypatch.setattr(settings, "identity_cache_ttl_seconds", 3600)
    with pytest.raises(RuntimeError, match="identity_cache_ttl_seconds"):
        validate_identity_settings()


def test_a_valid_bvbrc_configuration_boots(monkeypatch):
    monkeypatch.setattr(settings, "identity_provider", "bvbrc")
    validate_identity_settings()  # must not raise
