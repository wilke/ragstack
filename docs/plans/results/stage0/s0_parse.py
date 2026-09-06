"""Stage 0a step 3 -- parse, structural units, and the CORPUS MANIFEST HASH (SS4.2.5/4.2.7).

Produces, under ``$STAGE0_BIG/work``:

* ``docs.jsonl``      -- ``{"docno", "text"}``; ``text`` is exactly the indexed text
  ``title \\n\\n abstract \\n\\n body`` (``stage1_common.doc_text``), the same string every
  Phase-0 leg indexed and the string every char offset in this study refers to.
* ``units.jsonl``     -- ``{"docno", "units": [{"i","title","cls","start_char","end_char"}]}``
  from ``pilot_common.units_for_article`` -- D1's structural units. Text is NOT stored;
  it is ``docs.jsonl`` text sliced by the offsets, which is the point.
* ``manifest.json``   -- sorted ``(pmcid, sha256(file bytes))`` pairs and the sha256 OVER
  that sorted list. SS4.2.7 deliberately upgrades the Phase-0 paths-only convention:
  "same ids, different bytes" is caught. **Computed before any embedding.**

A document with an empty parsed body is excluded and counted (SS4.2.5).
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import pathlib
import time
import xml.etree.ElementTree as ET

import s0_common as C

_state: dict = {}


def init_worker():
    import sys
    sys.path.insert(0, str(C.STAGE1))
    sys.path.insert(0, str(C.PILOTS))
    import stage1_common as S
    S.pin_repo()                                   # P.1: in EVERY worker initialiser
    import pilot_common as P
    from ragstack.ingestion.jats import article_prose, front_meta, section_text
    _state.update(S=S, P=P, article_prose=article_prose, front_meta=front_meta,
                  section_text=section_text)


def work(path_str: str):
    p = pathlib.Path(path_str)
    docno = p.stem[3:]
    raw = p.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        root = ET.fromstring(raw)
    except Exception as e:  # noqa: BLE001
        return {"docno": docno, "sha256": digest, "err": f"parse:{type(e).__name__}"}
    try:
        _abstract, body = _state["article_prose"](root)
        text = _state["S"].doc_text(root, _state["article_prose"], _state["front_meta"])
        _dt, units = _state["P"].units_for_article(
            root, _state["section_text"], _state["article_prose"], _state["front_meta"])
    except Exception as e:  # noqa: BLE001
        return {"docno": docno, "sha256": digest, "err": f"units:{type(e).__name__}"}
    # SS4.2.5: "a document with an empty parsed body is excluded and counted".
    if not body or not body.strip():
        return {"docno": docno, "sha256": digest, "err": "empty_body"}
    if not text or not text.strip():
        return {"docno": docno, "sha256": digest, "err": "empty_text"}
    # units_for_article builds its own doc_text; assert it is the indexed string
    if _dt != text:
        return {"docno": docno, "sha256": digest, "err": "unit_text_mismatch"}
    title = _state["front_meta"](root).get("title", "")
    return {"docno": docno, "sha256": digest, "text": text, "title": title,
            "units": [{"i": u["i"], "title": u["title"], "cls": u["cls"],
                       "start_char": u["start_char"], "end_char": u["end_char"]}
                      for u in units]}


def main() -> None:
    files = sorted(str(p) for p in C.XML.glob("*.xml"))
    print("xml files:", len(files), flush=True)
    t0 = time.time()
    errs: dict[str, int] = {}
    pairs: list[tuple[str, str]] = []
    kept = 0
    with (open(C.WORK / "docs.jsonl", "w") as fd,
          open(C.WORK / "units.jsonl", "w") as fu,
          mp.Pool(32, initializer=init_worker) as pool):
        for k, r in enumerate(pool.imap_unordered(work, files, chunksize=16)):
            if "err" in r:
                errs[r["err"]] = errs.get(r["err"], 0) + 1
                continue
            pairs.append((r["docno"], r["sha256"]))
            fd.write(json.dumps({"docno": r["docno"], "title": r["title"],
                                 "text": r["text"]}) + "\n")
            fu.write(json.dumps({"docno": r["docno"], "units": r["units"]}) + "\n")
            kept += 1
            if (k + 1) % 5000 == 0:
                print(f"  {k+1}/{len(files)} {time.time()-t0:.0f}s", flush=True)
    pairs.sort(key=lambda x: int(x[0]))
    blob = "\n".join(f"{a} {b}" for a, b in pairs)
    manifest_hash = hashlib.sha256(blob.encode()).hexdigest()
    C.atomic_json(C.WORK / "manifest.json", {
        "n_docs": kept, "excluded": errs,
        "manifest_sha256": manifest_hash,
        "convention": "sha256 over '\\n'.join(f'{pmcid} {sha256(file_bytes)}') sorted by int(pmcid)",
        "pairs": pairs})
    print(json.dumps({"kept": kept, "excluded": errs,
                      "manifest_sha256": manifest_hash}, indent=1), flush=True)


if __name__ == "__main__":
    main()
