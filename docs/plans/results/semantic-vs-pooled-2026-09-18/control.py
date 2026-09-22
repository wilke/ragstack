"""CONTROL: run the SAME arm twice.

If legacy-vs-legacy Spearman is ~1.0, the legacy-vs-pooled 0.43 is a real method
difference. If it is also ~0.4, legacy is simply not reproducible and the
comparison measures noise. Legacy applies no distance rounding; pooled rounds to
6 decimals precisely to be reproducible — so this is the test that separates them.
"""
import json, sys
sys.path.insert(0, "/opt/ragstack/scripts")
from ragstack.ingestion.chunkers import make_chunker, sentence_spans, _cosine_distance
from ragstack.ingestion.embed_bridge import SyncEmbedBridge
from ragstack.ingestion.loaders import Document
import ingest_shard

URLS = ["http://localhost:9001", "http://localhost:9002"]
MODEL = "Salesforce/SFR-Embedding-Mistral"

def spearman(a, b):
    def rank(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        r = [0.0]*len(xs)
        for pos, i in enumerate(order): r[i] = float(pos)
        return r
    ra, rb = rank(a), rank(b); n = len(a)
    ma, mb = sum(ra)/n, sum(rb)/n
    num = sum((x-ma)*(y-mb) for x, y in zip(ra, rb))
    da = sum((x-ma)**2 for x in ra) ** 0.5
    db = sum((y-mb)**2 for y in rb) ** 0.5
    return num/(da*db) if da and db else float("nan")

docs = [Document(id=f"d{i}", content=json.loads(l)["text"], metadata={}, source="t")
        for i, l in enumerate(open(f"{sys.argv[1]}/shard.jsonl", encoding="utf-8"))]
_a = ingest_shard.parse_args(
    ["/dev/null", "--qdrant-url", "http://127.0.0.1:1", "--es-url", "http://127.0.0.1:1",
     "--chunk-method", "semantic", "--embedding-model", MODEL,
     "--embedding-url", URLS[0], "--embedding-url", URLS[1]])

def run(method):
    bridge = SyncEmbedBridge(lambda http: ingest_shard._build_embedder(_a, http))
    try:
        ch = make_chunker(method, embed_fn=bridge, buffer_size=2,
                          breakpoint_percentile_threshold=80.0, min_chunk_length=500)
        dists, spans = [], []
        for d in docs:
            sp = sentence_spans(d.content)
            v = ch._buffer_embeddings(d.content, sp)
            dd = [_cosine_distance(v[i], v[i+1]) for i in range(len(v)-1)]
            if ch.distance_round is not None:
                dd = [round(x, ch.distance_round) for x in dd]
            dists += dd
            spans.append([(c.start_char, c.end_char) for c in ch.chunk(d)])
        return dists, spans
    finally:
        bridge.close()

for method in ("semantic", "semantic_pooled"):
    d1, s1 = run(method)
    d2, s2 = run(method)
    same = sum(1 for a, b in zip(d1, d2) if a == b)
    print(f"{method:16s} run1-vs-run2 Spearman={spearman(d1, d2):.4f}  "
          f"identical distances {same}/{len(d1)}  spans identical={s1 == s2}")
