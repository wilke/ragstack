"""Unit tests for the token-counter backends and budget resolution."""
from __future__ import annotations

import httpx
import pytest

from ragstack.ingestion.tokenization import (
    EndpointTokenCounter,
    EstimatingTokenCounter,
    HFTokenCounter,
    make_token_counter,
    resolve_max_tokens,
)


# --------------------------------------------------------------------------- #
# EstimatingTokenCounter
# --------------------------------------------------------------------------- #
def test_estimating_counter_math():
    c = EstimatingTokenCounter(chars_per_token=4.0)
    assert c.count("") == 0
    assert c.count("abcd") == 1  # ceil(4/4)
    assert c.count("abcde") == 2  # ceil(5/4)
    assert c.count("a" * 40) == 10


def test_estimating_counter_rejects_bad_ratio():
    with pytest.raises(ValueError):
        EstimatingTokenCounter(chars_per_token=0)


def test_estimating_count_batch_defaults_to_map():
    c = EstimatingTokenCounter(chars_per_token=4.0)
    assert c.count_batch(["abcd", "abcdefgh"]) == [1, 2]


# --------------------------------------------------------------------------- #
# HFTokenCounter — uses the cached SFR tokenizer; skipped if transformers absent.
# --------------------------------------------------------------------------- #
def test_hf_counter_matches_encode():
    transformers = pytest.importorskip("transformers")
    model = "Salesforce/SFR-Embedding-Mistral"
    try:
        ref_tok = transformers.AutoTokenizer.from_pretrained(model)
    except Exception:  # noqa: BLE001 - tokenizer not cached/available in this env
        pytest.skip("SFR tokenizer not available")
    text = "Hello world, this is a tokenization test."
    expected = len(ref_tok.encode(text, add_special_tokens=False))
    c = HFTokenCounter(model=model)
    got = c.count(text)
    assert got > 0
    assert got == expected
    assert c.count("") == 0


# --------------------------------------------------------------------------- #
# EndpointTokenCounter — driven via httpx.MockTransport.
# --------------------------------------------------------------------------- #
def test_endpoint_counter_returns_count_and_sends_bearer():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        import json

        body = json.loads(request.content)
        seen["body"] = body
        return httpx.Response(200, json={"count": 7, "max_model_len": 4096, "tokens": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    c = EndpointTokenCounter(
        base_url="http://embed.test", model="m", api_key="SECRET", client=client
    )
    assert c.count("some text") == 7
    assert seen["auth"] == "Bearer SECRET"
    assert seen["url"] == "http://embed.test/tokenize"
    assert seen["body"] == {"model": "m", "prompt": "some text"}


def test_endpoint_counter_no_key_sends_no_bearer():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"count": 3})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    c = EndpointTokenCounter(base_url="http://embed.test", model="m", client=client)
    assert c.count("hi there") == 3
    assert seen["auth"] is None


def test_endpoint_counter_empty_text_short_circuits():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not be called for empty text")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    c = EndpointTokenCounter(base_url="http://embed.test", model="m", client=client)
    assert c.count("") == 0


# --------------------------------------------------------------------------- #
# resolve_max_tokens
# --------------------------------------------------------------------------- #
def test_resolve_max_tokens_explicit_wins(monkeypatch):
    # Explicit value returned without any network call.
    def boom(*a, **k):  # pragma: no cover
        raise AssertionError("no HTTP call when explicit is given")

    monkeypatch.setattr(httpx, "Client", boom)
    assert resolve_max_tokens(1234, base_url="http://x") == 1234


def test_resolve_max_tokens_parses_max_model_len(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200, json={"data": [{"id": "m", "max_model_len": 4096}]}
        )

    real_client = httpx.Client

    def factory(*a, **k):
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "Client", factory)
    # reserve default 16 -> 4080
    assert resolve_max_tokens(None, base_url="http://embed.test") == 4080
    assert resolve_max_tokens(None, base_url="http://embed.test", reserve=0) == 4096


def test_resolve_max_tokens_falls_back_on_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    real_client = httpx.Client

    def factory(*a, **k):
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "Client", factory)
    assert resolve_max_tokens(None, base_url="http://embed.test", default=4096) == 4096


def test_resolve_max_tokens_no_base_url_returns_default():
    assert resolve_max_tokens(None, base_url=None, default=2048) == 2048


# --------------------------------------------------------------------------- #
# make_token_counter factory + fallback chain
# --------------------------------------------------------------------------- #
def test_make_token_counter_estimate():
    c = make_token_counter("estimate", chars_per_token=5.0)
    assert isinstance(c, EstimatingTokenCounter)
    assert c.chars_per_token == 5.0


def test_make_token_counter_endpoint_requires_base_url():
    with pytest.raises(ValueError):
        make_token_counter("endpoint", model="m")


def test_make_token_counter_endpoint():
    c = make_token_counter("endpoint", model="m", base_url="http://x")
    assert isinstance(c, EndpointTokenCounter)


def test_make_token_counter_unknown_backend():
    with pytest.raises(ValueError):
        make_token_counter("bogus", model="m")


def test_make_token_counter_hf_requires_model():
    with pytest.raises(ValueError):
        make_token_counter("hf")


def test_make_token_counter_hf_falls_back_to_endpoint(monkeypatch):
    # Force the HF tokenizer load to fail, with an endpoint available → endpoint.
    def boom(self):
        raise RuntimeError("no transformers")

    monkeypatch.setattr(HFTokenCounter, "_tokenizer", boom)
    c = make_token_counter("hf", model="m", base_url="http://embed.test")
    assert isinstance(c, EndpointTokenCounter)


def test_make_token_counter_hf_falls_back_to_estimate(monkeypatch):
    # Force the HF load to fail with no endpoint → estimator.
    def boom(self):
        raise RuntimeError("no transformers")

    monkeypatch.setattr(HFTokenCounter, "_tokenizer", boom)
    c = make_token_counter("hf", model="m", base_url=None, chars_per_token=3.0)
    assert isinstance(c, EstimatingTokenCounter)
    assert c.chars_per_token == 3.0
