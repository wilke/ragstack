"""#754 wiring: API settings → deps, and the shard CLIs' --symbol-glossary flag
(default rules since #783) → pipeline + receipt. In-memory stores and a fake
embedder only."""
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


def test_deps_attacher_rules_by_default_and_off_when_set(monkeypatch, tmp_path) -> None:
    from ragstack.config import Settings

    monkeypatch.setattr(deps.settings, "symbol_glossary_extraction",
                        Settings(_env_file=None).symbol_glossary_extraction)  # type: ignore[call-arg]
    assert deps._build_glossary_attacher() is not None  # #783: on by default
    monkeypatch.setattr(deps.settings, "symbol_glossary_extraction", "off")
    assert deps._build_glossary_attacher() is None
    monkeypatch.setattr(deps.settings, "symbol_glossary_extraction", "rules")
    monkeypatch.setattr(deps.settings, "symbol_glossary_cache_path", str(tmp_path / "c.jsonl"))
    att = deps._build_glossary_attacher()
    assert att is not None and att.cache is not None
    monkeypatch.setattr(deps.settings, "symbol_glossary_extraction", "bogus")
    assert deps._build_glossary_attacher() is None  # a typo never takes the API down


def test_settings_defaults_extract_rules_and_leave_the_query_alone() -> None:
    """#783: extraction defaults to rules; the boost stays 0, so the BM25 query
    is the pre-#754 one until a tenant raises it."""
    from ragstack.config import Settings

    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.symbol_glossary_extraction == "rules" and s.text_symbol_glossary_boost == 0.0


def test_cli_flags_default_rules() -> None:
    """#783: both shard tools default to rules, so a hand run and a workflow
    that omits the input agree with the API's default."""
    a = ingest_shard.parse_args(["x.jsonl", "--qdrant-url", "http://127.0.0.1:1",
                                 "--es-url", "http://127.0.0.1:1"])
    b = embed_shard.parse_args(["x.jsonl"])
    for args in (a, b):
        assert args.symbol_glossary == "rules" and args.symbol_glossary_cache == ""
        assert args.symbol_glossary_config == ""
    for mod, base in ((ingest_shard, ["x.jsonl", "--qdrant-url", "http://127.0.0.1:1",
                                      "--es-url", "http://127.0.0.1:1"]),
                      (embed_shard, ["x.jsonl"])):
        assert mod.parse_args([*base, "--symbol-glossary", "off"]).symbol_glossary == "off"


# A definition, then (several chunks later) a chunk that states the symbol's
# value: the chunk the default attach_only_with_value config gives a glossary.
DEFINING = ("In this model, where Qb is the breathing flow rate of one person. "
            + "pad words here. " * 20
            + "Table 1 lists the parameters: Qb = 0.5 m3/h for a resting adult.")


def _shard(tmp_path: Path, text: str | None = None) -> str:
    p = tmp_path / "s0.jsonl"
    text = text or "where Qb is the breathing flow rate. " + "pad words here. " * 20 + "Qb = 0.5 m3/h."
    p.write_text(json.dumps({"text": text, "path": "/abs/a.pdf", "metadata": {}}) + "\n", encoding="utf-8")
    return str(p)


class _RecordingEmbedder(_FakeEmbedder):
    def __init__(self) -> None:
        self.seen: list[str] = []

    async def embed(self, texts):  # noqa: ANN001
        self.seen.extend(texts)
        return await super().embed(texts)


def _run_ingest_shard(tmp_path: Path, *extra: str, embedder: _FakeEmbedder | None = None,
                      text: str | None = None) -> ShardReceipt:
    out = tmp_path / "receipt.json"
    argv = [_shard(tmp_path, text), "--vector-backend", "memory", "--text-backend", "memory",
            "--qdrant-url", "http://127.0.0.1:1", "--es-url", "http://127.0.0.1:1",
            "--chunk-method", "fixed", "--chunk-size", "120", "--chunk-overlap", "0",
            "--embedding-model", "", "--chunk-token-counter", "estimate", "--out", str(out), *extra]
    emb = embedder or _FakeEmbedder()
    ingest_shard._build_embedder = lambda args, http: emb
    assert ingest_shard.main(argv) == 0
    off = "off" in extra
    assert ("enrichment" in json.loads(out.read_text(encoding="utf-8"))) == (not off)
    return ShardReceipt.load(out)


def test_ingest_shard_off_receipt_has_no_enrichment(tmp_path) -> None:
    assert _run_ingest_shard(tmp_path, "--symbol-glossary", "off").enrichment is None


def test_ingest_shard_default_records_rules_enrichment(tmp_path) -> None:
    """#783: the default run records the glossary step on its receipt
    (ADR-0011), exactly as an explicit ``--symbol-glossary rules`` does."""
    for extra in ((), ("--symbol-glossary", "rules")):
        r = _run_ingest_shard(tmp_path, *extra)
        assert r.enrichment and r.enrichment["symbol_glossary"]["extractor"] == "rules/2"
        assert r.enrichment["symbol_glossary"]["applied_batches"] == 1


def test_default_rules_changes_no_chunk_id_content_or_embedded_text(tmp_path) -> None:
    """#783: turning extraction on by default must not move anything a
    collection is identified or searched by. The same shard, run with the new
    default and with ``off``, yields the same chunk ids and the same embedded
    texts (the glossary is never embedded; ids are fixed at chunking, before the
    glossary step). Collection identity (spec_hash) is covered by
    test_symbol_glossary_provenance."""
    on_emb, off_emb = _RecordingEmbedder(), _RecordingEmbedder()
    (tmp_path / "on").mkdir()
    (tmp_path / "off").mkdir()
    on = _run_ingest_shard(tmp_path / "on", embedder=on_emb, text=DEFINING)
    off = _run_ingest_shard(tmp_path / "off", "--symbol-glossary", "off", embedder=off_emb,
                            text=DEFINING)
    assert on.chunk_ids and on.chunk_ids == off.chunk_ids
    assert on_emb.seen and on_emb.seen == off_emb.seen
    assert on.enrichment["symbol_glossary"]["chunks_with_glossary"] >= 1
