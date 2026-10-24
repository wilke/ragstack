"""``size_unit`` reaches the chunker from all three bulk tools (#318 P1).

``embed_shard.py`` and ``ingest_jsonl.py`` have no registry spec, so for them
``--chunk-size-unit`` is the source. ``ingest_shard.py`` writes into a registry
collection, where ``size_unit`` is collection identity: it reads
``chunk_params["size_unit"]`` from the entry and has no flag.

Offline: the estimator counter is chosen explicitly and nothing is embedded.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import embed_shard as embed_shard_tool  # noqa: E402
import ingest_jsonl as ingest_jsonl_tool  # noqa: E402
import ingest_shard as ingest_shard_tool  # noqa: E402

_OFFLINE = ["--chunk-token-counter", "estimate", "--embedding-model", ""]


def _ingest_shard_args(*extra: str):
    return ingest_shard_tool.parse_args(
        ["x.jsonl", "--qdrant-url", "http://127.0.0.1:1", "--es-url", "http://127.0.0.1:1",
         *_OFFLINE, *extra]
    )


def _embed_shard_args(*extra: str):
    return embed_shard_tool.parse_args(["x.jsonl", *_OFFLINE, *extra])


# --------------------------------------------------------------------------- #
# embed_shard / ingest_jsonl: no registry spec, so the CLI flag is the source
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("method", ["sentence", "words"])
def test_embed_shard_default_is_chars(method):
    args = _embed_shard_args("--chunk-method", method)
    assert args.chunk_size_unit == "chars"
    assert embed_shard_tool._build_chunker(args).size_unit == "chars"


@pytest.mark.parametrize("method", ["sentence", "words"])
def test_embed_shard_tokens_is_threaded(method):
    args = _embed_shard_args(
        "--chunk-method", method, "--chunk-size", "128", "--chunk-size-unit", "tokens"
    )
    chunker = embed_shard_tool._build_chunker(args)
    assert chunker.size_unit == "tokens"
    assert chunker.chunk_size == 128


def test_embed_shard_bad_unit_is_rejected_by_argparse():
    with pytest.raises(SystemExit):
        _embed_shard_args("--chunk-size-unit", "token")


# --------------------------------------------------------------------------- #
# ingest_shard: size_unit is collection identity, read from the registry entry
# --------------------------------------------------------------------------- #
class _Spec:
    """Just the attribute ingest_shard reads off a CollectionSpec."""

    def __init__(self, chunk_params):
        self.chunk_params = chunk_params


def test_ingest_shard_no_longer_accepts_the_flag():
    """A second source for an identity value is how #609 happened: the entry is
    the one source, so the flag must not exist on the registry-backed tool."""
    with pytest.raises(SystemExit):
        _ingest_shard_args("--chunk-method", "sentence", "--chunk-size-unit", "tokens")


@pytest.fixture
def spy_build(monkeypatch):
    """Record the size_unit ingest_shard hands build_chunker (and still build)."""
    seen: dict = {}
    real = ingest_shard_tool.build_chunker

    def spy(*a, **kw):
        seen["size_unit"] = kw.get("size_unit")
        return real(*a, **kw)

    monkeypatch.setattr(ingest_shard_tool, "build_chunker", spy)
    return seen


@pytest.mark.parametrize("method", ["sentence", "words"])
def test_ingest_shard_reads_tokens_from_the_spec(spy_build, method):
    args = _ingest_shard_args("--chunk-method", method, "--chunk-size", "128")
    chunker = ingest_shard_tool._build_chunker(args, _Spec({"size_unit": "tokens"}))
    assert spy_build["size_unit"] == "tokens"
    assert chunker.size_unit == "tokens"
    assert chunker.chunk_size == 128


@pytest.mark.parametrize("method", ["sentence", "words"])
@pytest.mark.parametrize("spec", [None, _Spec(None), _Spec({}), _Spec({"buffer_size": 3})],
                         ids=["no-spec", "params-none", "params-empty", "key-absent"])
def test_ingest_shard_absent_is_chars(spy_build, method, spec):
    args = _ingest_shard_args("--chunk-method", method)
    chunker = ingest_shard_tool._build_chunker(args, spec)
    assert spy_build["size_unit"] == "chars"
    assert chunker.size_unit == "chars"


@pytest.mark.parametrize("bad", ["token", "Tokens", 512, ["tokens"]])
def test_ingest_shard_bad_value_names_the_key(bad):
    args = _ingest_shard_args("--chunk-method", "sentence")
    with pytest.raises(SystemExit, match=r"chunk_params\['size_unit'\]"):
        ingest_shard_tool._build_chunker(args, _Spec({"size_unit": bad}))


@pytest.mark.parametrize("method", ["fixed", "fixed_token"])
def test_ingest_shard_junk_size_unit_on_other_methods_is_not_refused(
    spy_build, monkeypatch, method
):
    """`create_collection` persists `chunk_params` for any method but validates
    them only for the methods that use them, so a `fixed` collection can
    legitimately carry junk here. Reading it unconditionally would turn that into
    a refused shard on the path every current collection uses — the same
    junk-tolerance rule as `_semantic_params`."""
    from ragstack.ingestion import chunker_config
    from tests.unit.test_chunkers_semantic import _HFLikeCounter

    monkeypatch.setattr(chunker_config, "make_token_counter", lambda *_a, **_k: _HFLikeCounter())
    extra = ["--embedding-model", "some/model", "--chunk-max-tokens", "4096"] if method == "fixed_token" else []
    args = _ingest_shard_args("--chunk-method", method, *extra)
    assert ingest_shard_tool._build_chunker(args, _Spec({"size_unit": "garbage"})) is not None
    assert spy_build["size_unit"] == "chars"


def test_ingest_jsonl_exposes_the_flag():
    """ingest_jsonl builds its parser inside main(); --help is the offline probe."""
    out = subprocess.run(
        [sys.executable, str(_SCRIPTS / "ingest_jsonl.py"), "--help"],
        capture_output=True, text=True, timeout=120, check=True,
    ).stdout
    assert "--chunk-size-unit" in out
    assert "{chars,tokens}" in out


# --------------------------------------------------------------------------- #
# ingest_jsonl: keeps the flag, but resolves a registry entry when indexing —
# so the flag must not contradict the unit the entry records
# --------------------------------------------------------------------------- #
class _Target:
    def __init__(self, chunk_params):
        self.spec = _Spec(chunk_params)
        self.collection_id = "c-test"


def _ns(method: str, unit: str):
    import argparse

    return argparse.Namespace(chunk_method=method, chunk_size_unit=unit)


@pytest.mark.parametrize(
    "method,unit,params",
    [
        ("sentence", "chars", {}),                       # nothing recorded = chars
        ("sentence", "chars", None),
        ("sentence", "tokens", {"size_unit": "tokens"}),
        ("words", "chars", {"size_unit": "chars"}),
        ("fixed", "tokens", {"size_unit": "garbage"}),   # method ignores the unit
        ("sentence", "tokens", "no-target"),             # --no-index: nothing to contradict
    ],
)
def test_ingest_jsonl_size_unit_agrees_with_entry(method, unit, params):
    target = None if params == "no-target" else _Target(params)
    ingest_jsonl_tool._refuse_size_unit_mismatch(_ns(method, unit), target)  # no raise


@pytest.mark.parametrize(
    "unit,params",
    [
        ("tokens", {}),                       # existing chars collection
        ("tokens", {"size_unit": "chars"}),
        ("chars", {"size_unit": "tokens"}),
    ],
)
def test_ingest_jsonl_refuses_a_contradicting_unit(capsys, unit, params):
    with pytest.raises(SystemExit) as ei:
        ingest_jsonl_tool._refuse_size_unit_mismatch(_ns("sentence", unit), _Target(params))
    assert ei.value.code == 2
    err = capsys.readouterr().err
    assert "c-test" in err and f"--chunk-size-unit {unit}" in err


def test_ingest_jsonl_refuses_a_bad_recorded_unit(capsys):
    with pytest.raises(SystemExit):
        ingest_jsonl_tool._refuse_size_unit_mismatch(
            _ns("words", "chars"), _Target({"size_unit": "token"})
        )
    assert "chunk_params['size_unit']" in capsys.readouterr().err
