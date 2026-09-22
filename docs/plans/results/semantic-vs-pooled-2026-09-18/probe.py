"""Does the REGISTRY ENTRY's buffer_size reach the chunker? Build-only, no I/O."""
import os, sys
sys.path.insert(0, "/opt/ragstack/scripts")
import ingest_shard
from ragstack.ops.ingest_target import resolve_or_exit

argv = ["/dev/null", "--collection-id", "sem-e2e",
        "--qdrant-url", os.environ["QDRANT_URL"], "--es-url", os.environ["ES_URL"],
        "--chunk-method", "semantic_pooled",
        "--embedding-model", "Salesforce/SFR-Embedding-Mistral",
        "--embedding-url", "http://localhost:9001",
        "--chunk-token-counter", "estimate"]
args = ingest_shard.parse_args(argv)
target = resolve_or_exit(args)
print("registry chunk_params :", target.spec.chunk_params)
ch = ingest_shard._build_chunker(args, target.spec, embed_fn=lambda t: [[0.1]*4 for _ in t])
print("chunker class         :", type(ch).__name__)
print("pool_sentences        :", ch.pool_sentences)
print("buffer_size ON CHUNKER:", ch.buffer_size)
print("percentile            :", ch.breakpoint_percentile_threshold)
print("min_chunk_length      :", ch.min_chunk_length)
print()
print("VERDICT:", "entry value reached the chunker" if ch.buffer_size == 2
      else f"FAILED — chunker has {ch.buffer_size}, registry says 2 (tool default is 3)")
