"""Stage 0a step 4 -- chunk the shared corpus into the six index arms (SS5.1, P.3).

``FixedTokenWindowChunker`` from the repo at the pinned commit, SFR tokenizer, token
counter backend asserted ``hf`` (never ``estimate``). ``budget_mode`` is pinned to
``"joined"`` in the manifest (P.1) -- it does not reach ``token_window``, and that
inertness is ASSERTED here rather than assumed, so the ``55a0fc2`` fill-default change
cannot silently move a boundary.

Output per arm: ``spans_<arm>.jsonl`` -- one line per document,
``{"docno", "spans": [[start_char, end_char, ntok_sfr], ...], "hdr": [...]}``.
Chunk TEXT is never stored: it is ``docs.jsonl`` text sliced by the span, which keeps the
char offsets and the embedded string provably the same object (D1/D4 depend on it).
For ``header512`` the per-chunk header string is stored, since it is part of the EMBEDDED
and RERANKED text but is NOT part of the indexed char span (SS5.1).
"""
from __future__ import annotations

import json
import multiprocessing as mp
import time

import s0_common as C

_state: dict = {}


def init_worker():
    import sys
    sys.path.insert(0, str(C.STAGE1))
    import stage1_common as S
    S.pin_repo()                                   # P.1: in EVERY worker initialiser
    from ragstack.ingestion.chunkers import DEFAULT_BUDGET_MODE, FixedTokenWindowChunker
    from ragstack.ingestion.tokenization import make_token_counter
    tc = make_token_counter("hf", model=C.SFR_MODEL)
    assert callable(getattr(tc, "_tokenizer", None)), (
        f"token counter backend is not hf: {type(tc).__name__}")
    _state["tc"] = tc
    _state["default_budget_mode"] = DEFAULT_BUDGET_MODE
    _state["chunkers"] = {
        key: FixedTokenWindowChunker(chunk_size=size, chunk_overlap=ov, token_counter=tc)
        for key, size, ov, _hdr in C.INDEX_ARMS}


def work(rec_json: str):
    from ragstack.models import Document
    rec = json.loads(rec_json)
    docno, text, title = rec["docno"], rec["text"], rec["title"]
    units = _state["units"].get(docno, [])
    doc = Document(id=docno, content=text)
    out = {}
    for key, _size, _ov, hdr in C.INDEX_ARMS:
        chunks = _state["chunkers"][key].chunk(doc)
        spans = [[c.start_char, c.end_char, _state["tc"].count(c.content)] for c in chunks]
        out[key] = {"spans": spans}
        if hdr:
            # PIN (SS5.1 is silent on straddling chunks): the section whose span contains
            # the chunk's start_char -- the same rule SS5.2 fixes for parent256.
            hs = []
            for s, _e, _n in spans:
                sec = ""
                for u in units:
                    if u["start_char"] <= s < u["end_char"]:
                        sec = u["title"] or u["cls"]
                        break
                hs.append(f"«{title} — {sec}»\n" if (title or sec) else "")
            out[key]["hdr"] = hs
    return docno, out


def _init_with_units(units_map):
    init_worker()
    _state["units"] = units_map


def assert_budget_mode_inert() -> dict:
    """P.1: prove ``budget_mode`` cannot reach ``token_window`` at this commit."""
    import inspect
    import sys
    sys.path.insert(0, str(C.STAGE1))
    import stage1_common as S
    S.pin_repo()
    from ragstack.ingestion.chunkers import DEFAULT_BUDGET_MODE, FixedTokenWindowChunker
    sig = list(inspect.signature(FixedTokenWindowChunker.__init__).parameters)
    assert "budget_mode" not in sig, f"token_window now takes budget_mode: {sig}"
    src = inspect.getsource(FixedTokenWindowChunker)
    assert "budget_mode" not in src, "budget_mode appears in FixedTokenWindowChunker source"
    return {"fixed_token_window_params": sig,
            "repo_DEFAULT_BUDGET_MODE_at_55a0fc2": DEFAULT_BUDGET_MODE,
            "pinned_budget_mode": C.BUDGET_MODE,
            "inert_for_token_window": True}


def main() -> None:
    inert = assert_budget_mode_inert()
    print(json.dumps(inert), flush=True)
    units_map = {}
    for line in open(C.WORK / "units.jsonl"):
        r = json.loads(line)
        units_map[r["docno"]] = r["units"]
    lines = open(C.WORK / "docs.jsonl").read().splitlines()
    print("docs:", len(lines), flush=True)
    writers = {k: open(C.CHUNKS / f"spans_{k}.jsonl", "w") for k in C.INDEX_KEYS}
    counts = {k: 0 for k in C.INDEX_KEYS}
    toks = {k: 0 for k in C.INDEX_KEYS}
    t0 = time.time()
    with mp.Pool(32, initializer=_init_with_units, initargs=(units_map,)) as pool:
        for i, (docno, out) in enumerate(pool.imap(work, lines, chunksize=8)):
            for k, v in out.items():
                writers[k].write(json.dumps({"docno": docno, **v}) + "\n")
                counts[k] += len(v["spans"])
                toks[k] += sum(s[2] for s in v["spans"])
            if (i + 1) % 5000 == 0:
                print(f"  {i+1}/{len(lines)} {time.time()-t0:.0f}s", flush=True)
    for w in writers.values():
        w.close()
    stats = {k: {"chunks": counts[k], "sfr_tokens": toks[k],
                 "vectors_per_doc": round(counts[k] / len(lines), 3)} for k in C.INDEX_KEYS}
    stats["_total_sfr_tokens"] = sum(toks.values())
    stats["_budget_mode"] = inert
    stats["_seconds"] = round(time.time() - t0, 1)
    C.atomic_json(C.WORK / "chunk_stats.json", stats)
    print(json.dumps(stats, indent=1), flush=True)


if __name__ == "__main__":
    main()
