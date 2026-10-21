"""#754 wiring: API settings → deps, and the shard CLIs' --symbol-glossary flag
(default off) → pipeline + receipt. In-memory stores and a fake embedder only."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from ragstack.api import deps
from ragstack.ingestion.receipts import ShardReceipt

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import embed_shard  # noqa: E402
import ingest_shard  # noqa: E402


class _FakeEmbedder:
    async def embed(self, texts):  # noqa: ANN001
        return [[1.0, 0.0] for _ in texts]


def test_deps_attacher_off_by_default_and_rules_when_set(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(deps.settings, "symbol_glossary_extraction", "off")
    assert deps._build_glossary_attacher() is None
    monkeypatch.setattr(deps.settings, "symbol_glossary_extraction", "rules")
    monkeypatch.setattr(deps.settings, "symbol_glossary_cache_path", str(tmp_path / "c.jsonl"))
    att = deps._build_glossary_attacher()
    assert att is not None and att.cache is not None
    monkeypatch.setattr(deps.settings, "symbol_glossary_extraction", "bogus")
    assert deps._build_glossary_attacher() is None  # a typo never takes the API down


def test_settings_defaults_are_off() -> None:
    from ragstack.config import Settings

    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.symbol_glossary_extraction == "off" and s.text_symbol_glossary_boost == 0.0


def test_cli_flags_default_off() -> None:
    a = ingest_shard.parse_args(["x.jsonl", "--qdrant-url", "http://127.0.0.1:1",
                                 "--es-url", "http://127.0.0.1:1"])
    b = embed_shard.parse_args(["x.jsonl"])
    for args in (a, b):
        assert args.symbol_glossary == "off" and args.symbol_glossary_cache == ""


def _shard(tmp_path: Path) -> str:
    p = tmp_path / "s0.jsonl"
    text = "where Qb is the breathing flow rate. " + "pad words here. " * 20 + "Qb = 0.5 m3/h."
    p.write_text(json.dumps({"text": text, "path": "/abs/a.pdf", "metadata": {}}) + "\n", encoding="utf-8")
    return str(p)


def _run_ingest_shard(tmp_path: Path, *extra: str) -> ShardReceipt:
    out = tmp_path / "receipt.json"
    argv = [_shard(tmp_path), "--vector-backend", "memory", "--text-backend", "memory",
            "--qdrant-url", "http://127.0.0.1:1", "--es-url", "http://127.0.0.1:1",
            "--chunk-method", "fixed", "--chunk-size", "120", "--chunk-overlap", "0",
            "--embedding-model", "", "--chunk-token-counter", "estimate", "--out", str(out), *extra]
    ingest_shard._build_embedder = lambda args, http: _FakeEmbedder()
    assert ingest_shard.main(argv) == 0
    assert ("enrichment" in json.loads(out.read_text(encoding="utf-8"))) == bool(extra)
    return ShardReceipt.load(out)


def test_ingest_shard_default_receipt_has_no_enrichment(tmp_path) -> None:
    assert _run_ingest_shard(tmp_path).enrichment is None


def test_ingest_shard_rules_records_enrichment(tmp_path) -> None:
    r = _run_ingest_shard(tmp_path, "--symbol-glossary", "rules")
    assert r.enrichment and r.enrichment["symbol_glossary"]["extractor"] == "rules/2"
