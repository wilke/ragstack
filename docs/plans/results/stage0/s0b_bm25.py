"""In-process BM25 over an index arm's chunks -- the ``bm25`` leg of r3 SS3.4.

Pinned to Elasticsearch's shape as production configures it: the mapping is ``text`` with
no analyzer, i.e. ES ``standard`` (UAX#29 word boundaries, lowercase, no stemming, no
stopwords), queried with a plain ``match`` (OR over the query's token occurrences), Lucene
BM25 with k1 = 1.2 and b = 0.75. ``s0b_common.BM25_PIN`` lists the *known departures* of
this tokenizer from UAX#29; ``s0b_es.py`` measures what they cost against the real thing.

Only the QUERY vocabulary is scored, which is what makes a 2.2-million-chunk index a
CPU-minutes job: a term that appears in no query can change no ranking.
"""
from __future__ import annotations

import bisect
import time

import numpy as np

import s0b_common as K


class CorpusTokens:
    """One tokenization of the whole corpus, shared by every arm.

    Per document: a term-id array and a matching start-offset array, both int32. A chunk's
    tokens are the ones whose *start* lies in ``[start_char, end_char)`` -- so a token that
    straddles a chunk boundary is charged to the chunk it starts in. (Elasticsearch would
    analyse the chunk's own text and see a truncated word there instead; the effect is one
    token per boundary and is one of the departures the concordance check measures.)
    """

    def __init__(self, docs: dict[str, str]) -> None:
        t0 = time.time()
        self.vocab: dict[str, int] = {}
        self.ids: dict[str, np.ndarray] = {}
        self.starts: dict[str, np.ndarray] = {}
        v = self.vocab
        for d, text in docs.items():
            ids, sts = [], []
            for m in K.BM25_TOKEN_RE.finditer(text.lower()):
                w = m.group(0)
                i = v.get(w)
                if i is None:
                    i = v[w] = len(v)
                ids.append(i)
                sts.append(m.start())
            self.ids[d] = np.asarray(ids, dtype=np.int32)
            self.starts[d] = np.asarray(sts, dtype=np.int32)
        self.seconds = round(time.time() - t0, 1)
        self.n_tokens = int(sum(len(x) for x in self.ids.values()))

    def slice_ids(self, docno: str, s: int, e: int) -> np.ndarray:
        st = self.starts[docno]
        a = bisect.bisect_left(st, s)
        b = bisect.bisect_left(st, e)
        return self.ids[docno][a:b]

    def term_ids(self, text: str) -> list[int]:
        """Query-side tokenization; unseen terms get fresh ids (df = 0, idf harmless)."""
        out = []
        for m in K.BM25_TOKEN_RE.finditer(text.lower()):
            w = m.group(0)
            i = self.vocab.get(w)
            if i is None:
                i = self.vocab[w] = len(self.vocab)
            out.append(i)
        return out


class ArmBM25:
    """BM25 postings for one arm, restricted to the query vocabulary."""

    def __init__(self, rows, ct: CorpusTokens, qterms: set[int],
                 headers: dict | None = None) -> None:
        t0 = time.time()
        n = len(rows)
        qmap = np.full(len(ct.vocab) + 8, -1, dtype=np.int32)
        self.terms = sorted(qterms)
        for j, t in enumerate(self.terms):
            qmap[t] = j
        V = len(self.terms)
        dl = np.zeros(n, dtype=np.int32)
        ch_i: list[np.ndarray] = []
        tm_i: list[np.ndarray] = []
        tf_i: list[np.ndarray] = []
        for i, (d, s, e, _nt) in enumerate(rows):
            ids = ct.slice_ids(d, s, e)
            if headers is not None:
                h = headers.get((d, s))
                if h:
                    ids = np.concatenate([np.asarray(ct.term_ids(h), dtype=np.int32), ids])
            dl[i] = ids.size
            if ids.size == 0:
                continue
            q = qmap[ids]
            q = q[q >= 0]
            if q.size == 0:
                continue
            cnt = np.bincount(q, minlength=V)
            nz = np.nonzero(cnt)[0]
            ch_i.append(np.full(nz.size, i, dtype=np.int32))
            tm_i.append(nz.astype(np.int32))
            tf_i.append(cnt[nz].astype(np.int32))
        self.n = n
        self.dl = dl
        self.avgdl = float(dl.mean()) if n else 0.0
        self.chunk = np.concatenate(ch_i) if ch_i else np.zeros(0, np.int32)
        self.term = np.concatenate(tm_i) if tm_i else np.zeros(0, np.int32)
        self.tf = np.concatenate(tf_i) if tf_i else np.zeros(0, np.int32)
        order = np.argsort(self.term, kind="stable")
        self.chunk, self.term, self.tf = (self.chunk[order], self.term[order],
                                          self.tf[order])
        self.tstart = np.searchsorted(self.term, np.arange(V), side="left")
        self.tend = np.searchsorted(self.term, np.arange(V), side="right")
        self.df = (self.tend - self.tstart).astype(np.int64)
        self.idf = np.log(1.0 + (n - self.df + 0.5) / (self.df + 0.5))
        # tf-normalised contribution, precomputed per posting
        norm = K.BM25_K1 * (1.0 - K.BM25_B + K.BM25_B * dl[self.chunk] /
                            (self.avgdl or 1.0))
        self.contrib = (self.tf * (K.BM25_K1 + 1.0) / (self.tf + norm)).astype(np.float32)
        self.term_index = {t: j for j, t in enumerate(self.terms)}
        self.seconds = round(time.time() - t0, 1)
        self.n_postings = int(self.chunk.size)

    def topk(self, query_term_ids: list[int], k: int):
        """Top-k chunk rows for one query. Returns (indices, scores) sorted desc."""
        qtf: dict[int, int] = {}
        for t in query_term_ids:
            j = self.term_index.get(t)
            if j is not None:
                qtf[j] = qtf.get(j, 0) + 1
        if not qtf:
            return np.zeros(0, np.int64), np.zeros(0, np.float32)
        chunks, vals = [], []
        for j, w in qtf.items():
            a, b = self.tstart[j], self.tend[j]
            if a == b:
                continue
            chunks.append(self.chunk[a:b])
            vals.append(self.contrib[a:b] * (self.idf[j] * w))
        if not chunks:
            return np.zeros(0, np.int64), np.zeros(0, np.float32)
        ch = np.concatenate(chunks)
        va = np.concatenate(vals).astype(np.float64)
        acc = np.bincount(ch, weights=va, minlength=self.n)
        nz = np.nonzero(acc)[0]
        if nz.size == 0:
            return np.zeros(0, np.int64), np.zeros(0, np.float32)
        kk = min(k, nz.size)
        part = np.argpartition(-acc[nz], kk - 1)[:kk]
        sel = nz[part]
        order = np.argsort(-acc[sel], kind="stable")
        sel = sel[order]
        return sel.astype(np.int64), acc[sel].astype(np.float32)
