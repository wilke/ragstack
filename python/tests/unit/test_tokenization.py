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


def test_estimating_counter_default_ratio_is_conservative():
    # The default divisor is deliberately low (2.5) so the zero-info fallback
    # OVER-counts dense text rather than letting a chunk slip over budget.
    assert EstimatingTokenCounter().chars_per_token == 2.5
    # 100 chars -> ceil(100/2.5)=40 tokens; the conservative default reports more
    # tokens than the looser 3.7 English ratio, so packing stops sooner.
    assert EstimatingTokenCounter().count("x" * 100) == 40
    assert EstimatingTokenCounter().count("x" * 100) > EstimatingTokenCounter(
        chars_per_token=3.7
    ).count("x" * 100)


def test_make_token_counter_estimate_default_is_conservative():
    from ragstack.ingestion.tokenization import make_token_counter

    assert make_token_counter("estimate").chars_per_token == 2.5


def test_estimating_counter_rejects_bad_ratio():
    with pytest.raises(ValueError):
        EstimatingTokenCounter(chars_per_token=0)


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


def test_endpoint_counter_lazy_client_is_thread_safe(monkeypatch):
    # Concurrent count() calls must share a single lazily-built client, not race
    # to construct several (chunking_compare chunks in a ThreadPoolExecutor).
    import threading
    from concurrent.futures import ThreadPoolExecutor

    constructed: list[object] = []
    real_client_cls = httpx.Client
    start = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 5})

    def counting_factory(*a, **k):
        # Widen the race window so a non-locked lazy init would construct twice.
        start.wait()
        inst = real_client_cls(transport=httpx.MockTransport(handler))
        constructed.append(inst)
        return inst

    monkeypatch.setattr(httpx, "Client", counting_factory)
    c = EndpointTokenCounter(base_url="http://embed.test", model="m")

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(c.count, "some text here") for _ in range(8)]
        start.set()
        results = [f.result() for f in futures]

    assert results == [5] * 8
    # Exactly one client constructed and reused; identity stable.
    assert len(constructed) == 1
    assert c._http() is constructed[0]


# --------------------------------------------------------------------------- #
# resolve_max_tokens
# --------------------------------------------------------------------------- #
def test_resolve_max_tokens_explicit_wins(monkeypatch):
    # Explicit value short-circuits with no network call, but is treated as the
    # model window: the same `reserve` headroom is subtracted (1234 - 16).
    def boom(*a, **k):  # pragma: no cover
        raise AssertionError("no HTTP call when explicit is given")

    monkeypatch.setattr(httpx, "Client", boom)
    assert resolve_max_tokens(1234, base_url="http://x") == 1218
    # Explicit honours a custom reserve too.
    assert resolve_max_tokens(1234, base_url="http://x", reserve=34) == 1200


def test_resolve_max_tokens_explicit_subtracts_reserve_and_floors():
    # The flag is the model window; the chunker keeps `reserve` headroom, so the
    # returned budget is window - reserve, floored at >= 1 for tiny windows.
    assert resolve_max_tokens(100, base_url=None, reserve=16) == 84
    # window <= reserve must not produce a zero/negative budget.
    assert resolve_max_tokens(10, base_url=None, reserve=16) == 1
    assert resolve_max_tokens(16, base_url=None, reserve=16) == 1
    assert resolve_max_tokens(1, base_url=None, reserve=16) == 1


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


def test_resolve_max_tokens_clamps_tiny_max_model_len(monkeypatch):
    # max_model_len <= reserve must not silently disable capping with a
    # zero/negative budget — it's clamped to >= 1.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "m", "max_model_len": 8}]})

    real_client = httpx.Client

    def factory(*a, **k):
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "Client", factory)
    # 8 - 16 = -8 -> clamped to 1
    assert resolve_max_tokens(None, base_url="http://embed.test", reserve=16) == 1


def test_resolve_max_tokens_empty_data_returns_default(monkeypatch):
    # An empty {"data": []} (or absent max_model_len) falls through to default
    # without crashing.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    real_client = httpx.Client

    def factory(*a, **k):
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "Client", factory)
    assert resolve_max_tokens(None, base_url="http://embed.test", default=4096) == 4096


def test_resolve_max_tokens_missing_field_returns_default(monkeypatch):
    # data present but no max_model_len key -> default, no crash.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "m"}]})

    real_client = httpx.Client

    def factory(*a, **k):
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "Client", factory)
    assert resolve_max_tokens(None, base_url="http://embed.test", default=777) == 777


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
