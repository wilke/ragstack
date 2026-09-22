"""semantic vs semantic_pooled on IDENTICAL inputs, through the real chunkers.

Measures what the mechanism actually consumes: the percentile rule uses only the
RANK ORDER of consecutive distances, so Spearman on the two distance series is
the test of whether the methods can disagree about boundaries at all.
"""
import json, sys
sys.path.insert(0, "/opt/ragstack/scripts")
import httpx
from ragstack.ingestion.chunkers import make_chunker, sentence_spans, _cosine_distance
from ragstack.ingestion.embed_bridge import SyncEmbedBridge
from ragstack.ingestion.loaders import Document
from ragstack.ingestion.tokenization import make_token_counter

URLS = ["http://localhost:9001", "http://localhost:9002"]
MODEL = "Salesforce/SFR-Embedding-Mistral"
tc = make_token_counter("hf", model=MODEL)

class Counting:
    """Wraps the bridge; records how many TEXTS and TOKENS each arm embeds."""
    def __init__(self, inner): self.inner, self.texts, self.tokens = inner, 0, 0
    def __call__(self, texts):
        self.texts += len(texts)
        self.tokens += sum(tc.count(t) for t in texts)
        return self.inner(texts)

def spearman(a, b):
    def rank(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        r = [0.0]*len(xs)
        for pos, i in enumerate(order): r[i] = float(pos)
        return r
    ra, rb = rank(a), rank(b)
    n = len(a)
    ma, mb = sum(ra)/n, sum(rb)/n
    num = sum((x-ma)*(y-mb) for x, y in zip(ra, rb))
    da = sum((x-ma)**2 for x in ra) ** 0.5
    db = sum((y-mb)**2 for y in rb) ** 0.5
    return num/(da*db) if da and db else float("nan")

docs = [Document(id=f"d{i}", content=json.loads(l)["text"], metadata={}, source="t")
        for i, l in enumerate(open(f"{sys.argv[1]}/shard.jsonl", encoding="utf-8"))]

# Use the TOOL's own embedder builder, so the comparison runs on the same path
# a real shard does rather than a lookalike.
import ingest_shard
_a = ingest_shard.parse_args(
    ["/dev/null", "--qdrant-url", "http://127.0.0.1:1", "--es-url", "http://127.0.0.1:1",
     "--chunk-method", "semantic_pooled", "--embedding-model", MODEL,
     "--embedding-url", URLS[0], "--embedding-url", URLS[1]])
bridge = SyncEmbedBridge(lambda http: ingest_shard._build_embedder(_a, http))
try:
    arms = {}
    for method in ("semantic", "semantic_pooled"):
        counter = Counting(bridge)
        ch = make_chunker(method, embed_fn=counter, buffer_size=2,
                          breakpoint_percentile_threshold=80.0, min_chunk_length=500)
        spans_out, dists = [], []
        for d in docs:
            sp = sentence_spans(d.content)
            vecs = ch._buffer_embeddings(d.content, sp)
            dd = [_cosine_distance(vecs[i], vecs[i+1]) for i in range(len(vecs)-1)]
            if ch.distance_round is not None:
                dd = [round(x, ch.distance_round) for x in dd]
            dists.append(dd)
            spans_out.append([(c.start_char, c.end_char) for c in ch.chunk(d)])
        arms[method] = dict(spans=spans_out, dists=dists,
                            texts=counter.texts, tokens=counter.tokens)
finally:
    bridge.close()

L, P = arms["semantic"], arms["semantic_pooled"]
print(f"{'':22s} {'semantic':>14s} {'semantic_pooled':>16s}")
print(f"{'texts embedded':22s} {L['texts']:>14d} {P['texts']:>16d}")
print(f"{'TOKENS embedded':22s} {L['tokens']:>14d} {P['tokens']:>16d}")
print(f"{'ratio (legacy/pooled)':22s} {L['tokens']/max(P['tokens'],1):>14.2f}x")
print()
tot_i = tot_u = 0
for i, d in enumerate(docs):
    sl, sp = set(L["spans"][i]), set(P["spans"][i])
    inter, uni = len(sl & sp), len(sl | sp)
    tot_i += inter; tot_u += uni
    rho = spearman(L["dists"][i], P["dists"][i])
    print(f"doc{i}: n_sent={len(L['dists'][i])+1} "
          f"chunks legacy={len(sl)} pooled={len(sp)} "
          f"Jaccard={inter/uni:.3f} Spearman(rho)={rho:.4f}")
    if sl != sp:
        print(f"      legacy spans {sorted(sl)}")
        print(f"      pooled spans {sorted(sp)}")
print()
print(f"OVERALL span Jaccard: {tot_i/max(tot_u,1):.3f}")
allL = [x for dd in L["dists"] for x in dd]
allP = [x for dd in P["dists"] for x in dd]
print(f"OVERALL Spearman over all {len(allL)} consecutive-pair distances: {spearman(allL, allP):.4f}")
