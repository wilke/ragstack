"""Shared pieces of the glossary-benchmark round-2 harness (items 2c and 2d).

Pure and offline: reading chunk exports, grouping documents into papers, the
value-statement test, the gold normalisation, the lexical-leak tokens, the
private-config loader and the provenance block. No network, no store, no model.

Nothing private lives here. Corpus paths, gateway URLs, model ids and the frozen
prereg sha come from the private config (``--private-config`` or env
``GLOSSBENCH_R2_CONFIG``); corpora are named by their set alias (``O``, ``A``,
``P``) only.

**Chunk exports.** JSONL (optionally ``.gz``), one chunk per line, in either the
archive shape (``id``, ``doc_id``, ``content``, ``start_char``, ``end_char``,
``metadata``) or an Elasticsearch hit (``{"_id", "_source": {...}}``, the
``chunk_id`` field preferred over ``_id``). Every chunk must carry a non-empty
id, ``doc_id`` and its offsets; ``content`` may not be ``null``.

**Papers.** The clustering unit (prereg Terms): ``paper_key = "doc_id"`` makes
every document its own paper (ASM-1000, papers-OC); ``"path_root"`` groups the
documents whose ``metadata.source_path`` share the part before ``#`` (OA-1000:
an article ``PMC123`` with its float units ``PMC123#table-2``).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO_PY = Path(__file__).resolve().parents[2]
if str(_REPO_PY) not in sys.path:
    sys.path.insert(0, str(_REPO_PY))

from ragstack.ingestion.reassembly import Reassembled, reassemble  # noqa: E402
from ragstack.ingestion.symbol_glossary import (  # noqa: E402
    _BARS,
    _NOT_TOKEN_BEFORE,
    _UNITS,
    canonical_text,
    value_pattern,
)

REPO = _REPO_PY.parent.resolve()
CONFIG_ENV = "GLOSSBENCH_R2_CONFIG"
SETS = ("O", "A", "P")
#: Every model call site enforces this in code; a config asking for more is refused.
MAX_INFLIGHT = 4


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def file_sha(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_json_atomic(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def refuse_in_repo(path: Path, what: str) -> None:
    """Private outputs (article text, quotes) never go inside the public repo."""
    p = path.resolve()
    if p == REPO or REPO in p.parents:
        raise SystemExit(f"refusing to write {what} inside the repository: {path}")


def experiment_provenance() -> dict[str, Any]:
    from ragstack.provenance import experiment_provenance as ep

    return ep(repo=str(REPO))


def citable(prov: Mapping[str, Any]) -> bool:
    return bool(prov.get("commit")) and prov.get("dirty") is False and prov.get("untracked") == 0


def string_values(obj: Any) -> Iterator[str]:
    """Every string (keys included) inside a JSON-like object: what a counts-only
    artifact is checked by before it may go public (prereg §10)."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, Mapping):
        for k, v in obj.items():
            yield str(k)
            yield from string_values(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from string_values(v)


# ------------------------------------------------------------------ config

def load_config(path: str | None) -> tuple[dict[str, Any], str]:
    """The private config and its sha256. Required: there are no defaults that
    resolve to a corpus or an endpoint."""
    path = path or os.environ.get(CONFIG_ENV)
    if not path:
        raise SystemExit(f"a private config is required: pass --private-config or set {CONFIG_ENV}")
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"private config not found: {p}")
    cfg = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise SystemExit("private config: expected a JSON object")
    return cfg, file_sha(p)


def corpus_config(cfg: Mapping[str, Any], set_: str) -> dict[str, Any]:
    if set_ not in SETS:
        raise SystemExit(f"unknown set {set_!r}; expected one of {', '.join(SETS)}")
    c = (cfg.get("corpora") or {}).get(set_)
    if not isinstance(c, dict) or not c.get("chunks"):
        raise SystemExit(f"private config: corpora.{set_}.chunks is required")
    return c


def out_dir(cfg: Mapping[str, Any], override: str | None = None) -> Path:
    d = override or cfg.get("out_dir")
    if not d:
        raise SystemExit("private config: out_dir is required (outputs are private)")
    p = Path(d)
    refuse_in_repo(p, "the round-2 outputs")
    p.mkdir(parents=True, exist_ok=True)
    return p


# ------------------------------------------------------------------ chunks

@dataclass(frozen=True)
class ChunkRow:
    chunk_id: str
    doc_id: str
    content: str
    start_char: int
    end_char: int
    source_path: str
    title: str
    #: the export's chunk metadata (the partner export reads a PMC id / DOI from it)
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False)


def _open_text(path: str | Path) -> Any:
    p = str(path)
    return gzip.open(p, "rt", encoding="utf-8") if p.endswith(".gz") else open(p, encoding="utf-8")


def parse_chunk(obj: Mapping[str, Any], where: str) -> ChunkRow:
    src = obj.get("_source") if isinstance(obj.get("_source"), Mapping) else obj
    cid = src.get("chunk_id") or src.get("id") or obj.get("_id")
    doc = src.get("doc_id")
    content = src.get("content")
    start, end = src.get("start_char"), src.get("end_char")
    if not cid or not doc or content is None or start is None or end is None:
        raise SystemExit(f"{where}: a chunk row needs an id, doc_id, content, start_char and end_char")
    md = src.get("metadata") or {}
    return ChunkRow(str(cid), str(doc), str(content), int(start), int(end),
                    str(md.get("source_path") or ""), str(md.get("title") or ""),
                    dict(md) if isinstance(md, Mapping) else {})


def read_chunks(paths: Iterable[str | Path]) -> list[ChunkRow]:
    rows: list[ChunkRow] = []
    seen: set[str] = set()
    for path in paths:
        with _open_text(path) as fh:
            for i, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                row = parse_chunk(json.loads(line), f"{Path(path).name}:{i}")
                if row.chunk_id in seen:
                    raise SystemExit(f"{Path(path).name}:{i}: duplicate chunk id")
                seen.add(row.chunk_id)
                rows.append(row)
    return rows


def paper_of(row: ChunkRow, rule: str) -> str:
    if rule == "doc_id":
        return row.doc_id
    if rule == "path_root":
        return row.source_path.split("#", 1)[0] if row.source_path else row.doc_id
    raise SystemExit(f"unknown paper_key rule {rule!r} (doc_id | path_root)")


@dataclass
class Corpus:
    """One index's chunks, documents (reassembled) and papers."""

    chunks: dict[str, ChunkRow]
    by_doc: dict[str, list[ChunkRow]]
    docs: dict[str, Reassembled]
    paper_docs: dict[str, list[str]]
    doc_paper: dict[str, str]
    _norm_cache: dict[str, dict[str, str]] = field(default_factory=dict)
    _doc_norm_cache: dict[str, list[str]] = field(default_factory=dict)
    _tok_cache: dict[str, Counter[str]] = field(default_factory=dict)

    def paper_chunks(self, paper: str) -> list[ChunkRow]:
        return [c for d in self.paper_docs[paper] for c in self.by_doc[d]]

    def paper_texts(self, paper: str) -> list[str]:
        return [self.docs[d].text for d in self.paper_docs[paper]]

    def norm_chunks(self, paper: str) -> dict[str, str]:
        """chunk id -> gold-normalised content, for every chunk of the paper (cached)."""
        if paper not in self._norm_cache:
            self._norm_cache[paper] = {c.chunk_id: gold_norm(c.content) for c in self.paper_chunks(paper)}
        return self._norm_cache[paper]

    def norm_docs(self, paper: str) -> list[str]:
        """The gold-normalised text of each document of the paper (cached)."""
        if paper not in self._doc_norm_cache:
            self._doc_norm_cache[paper] = [gold_norm(t) for t in self.paper_texts(paper)]
        return self._doc_norm_cache[paper]

    def token_counts(self, paper: str) -> Counter[str]:
        if paper not in self._tok_cache:
            cnt: Counter[str] = Counter()
            for t in self.paper_texts(paper):
                cnt.update(leak_tokens(t))
            self._tok_cache[paper] = cnt
        return self._tok_cache[paper]

    def title(self, paper: str) -> str:
        for d in self.paper_docs[paper]:
            for c in self.by_doc[d]:
                if c.title:
                    return c.title
        return ""


def build_corpus(rows: list[ChunkRow], paper_rule: str) -> Corpus:
    by_doc: dict[str, list[ChunkRow]] = {}
    for r in rows:
        by_doc.setdefault(r.doc_id, []).append(r)
    docs: dict[str, Reassembled] = {}
    paper_docs: dict[str, list[str]] = {}
    doc_paper: dict[str, str] = {}
    for doc_id in sorted(by_doc):
        crs = sorted(by_doc[doc_id], key=lambda c: (c.start_char, c.end_char, c.chunk_id))
        by_doc[doc_id] = crs
        docs[doc_id] = reassemble(doc_id, [{"content": c.content, "start_char": c.start_char,
                                            "end_char": c.end_char} for c in crs])
        papers = {paper_of(c, paper_rule) for c in crs}
        if len(papers) != 1:
            raise SystemExit("a document's chunks name more than one paper (source_path differs)")
        p = papers.pop()
        doc_paper[doc_id] = p
        paper_docs.setdefault(p, []).append(doc_id)
    # The paper's first document (its article) first, then its units in id order.
    for p, ds in paper_docs.items():
        ds.sort(key=lambda d: (by_doc[d][0].source_path != p, by_doc[d][0].source_path, d))
    return Corpus({r.chunk_id: r for r in rows}, by_doc, docs, paper_docs, doc_paper)


# ------------------------------------------------------- value statements

_TOKEN_RUN = re.compile(rf"{_NOT_TOKEN_BEFORE}[\w{_BARS}]+")
_VALUE_TAIL = re.compile(r"\s*(?:\([^()\n]{0,20}\))?\s*[=≈≃~∼]\s*[-−+]?\s*\.?\d")
_HAS_LETTER = re.compile(r"[^\W\d_]")

#: Statistical tokens that do NOT make a chunk a value statement (pre-registered
#: stoplist): ``p = 0.03`` or ``n = 12`` is a test statistic or a count, not a
#: symbol-bound quantity of the paper. Compared in canonical form, case-sensitive
#: (``F`` the statistic is listed; ``f`` a frequency is not). Configurable per run
#: (``pool.vs_stoplist``); the pool header records the list used.
STAT_TOKENS: frozenset[str] = frozenset(
    canonical_text(t) for t in """p n N df R2 R² r t F z χ2 χ² chi2 CI SD SE SEM OR HR RR IQR AUC""".split())


def value_statements(text: str, stoplist: Iterable[str] | None = None) -> list[str]:
    """The tokens of ``text`` that state a value: canonical text, every token
    (a run of word characters and bars) that contains a letter and is not a
    unit or a stoplisted statistical token, followed by ``[=≈≃~∼]`` and a
    number — the library's :func:`~ragstack.ingestion.symbol_glossary.value_pattern`
    applied to the chunk's own tokens (prereg §3.1 step 1). Sorted, distinct.
    Uses no glossary. ``stoplist`` defaults to :data:`STAT_TOKENS`."""
    canon = canonical_text(text)
    stop = STAT_TOKENS if stoplist is None else frozenset(canonical_text(t) for t in stoplist)
    found: set[str] = set()
    for m in _TOKEN_RUN.finditer(canon):
        tok = m.group(0)
        if not _HAS_LETTER.search(tok) or tok in _UNITS or tok in stop:
            continue
        if _VALUE_TAIL.match(canon, m.end()) and value_pattern(tok).match(canon, m.start()):
            found.add(tok)
    return sorted(found)


_NUMBER = re.compile(r"\d")
NUMBER_WORDS = frozenset(
    """zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen
    fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty
    ninety hundred thousand million billion half third quarter twice double triple
    tenfold twofold threefold""".split())


def has_number(text: str) -> bool:
    if _NUMBER.search(text):
        return True
    return any(w in NUMBER_WORDS for w in re.findall(r"[a-z]+", text.casefold()))


def words_only_candidate(text: str, stoplist: Iterable[str] | None = None) -> bool:
    """An S3 candidate chunk: no value statement, yet a number is printed in it
    (the drafter then looks for a quantity named in words)."""
    return not value_statements(text, stoplist) and has_number(text)


# ------------------------------------------------------------ normalisation

def gold_norm(text: str) -> str:
    """The gold normalisation (part 4's rule): NFKC, casefold, all whitespace removed."""
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


_LEAK_TOKEN = re.compile(r"[a-z0-9]+")
#: A fixed English stop-word list for the lexical-leak guard (prereg §3.3).
STOP_WORDS = frozenset(
    """a about above after again against all also am an and any are as at be because been before
    being below between both but by can could did do does doing down during each few for from
    further had has have having he her here hers herself him himself his how i if in into is it
    its itself just me more most my myself no nor not now of off on once only or other our ours
    ourselves out over own same she should so some such than that the their theirs them
    themselves then there these they this those through to too under until up very was we were
    what when where which while who whom why will with would you your yours yourself yourselves
    s t was were what which whose within without per via vs i.e e.g""".split())


def leak_tokens(text: str) -> list[str]:
    """Tokens ``[a-z0-9]+`` after NFKC + casefold, stop words removed."""
    return [t for t in _LEAK_TOKEN.findall(unicodedata.normalize("NFKC", text).casefold())
            if t not in STOP_WORDS]
