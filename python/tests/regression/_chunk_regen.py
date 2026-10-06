"""Run the CURRENT ragstack chunking code over the regression inputs.

Executed by ``test_chunking_regression.py`` in a **subprocess**, so that ``HF_HOME`` /
``HF_HUB_OFFLINE`` take effect before ``transformers``/``huggingface_hub`` are imported.
In the pytest process another test may already have imported them with the host's cache
path. It also keeps the tokenizer call identical to stage0's:
``make_token_counter("hf", model="Salesforce/SFR-Embedding-Mistral")``, resolved
through the hub cache, not a local path.

Usage: ``python _chunk_regen.py <regression dir> <out.json>``

Exit codes: 0 means OK. ``EXIT_NO_TOKENIZER`` means the tokenizer can't be loaded
offline, and the test skips. Anything else is a crash, and the test fails.

The per-chunk ``hdr`` rule for ``header512`` is **harness** logic, copied verbatim from
``docs/plans/results/stage0/s0_chunk.py::work``. It is not library code, so it lives
here, not in ``ragstack``.
"""
from __future__ import annotations

import json
import pathlib
import sys

EXIT_NO_TOKENIZER = 3


def _tokenizer_revision(model: str) -> str | None:
    """The hub snapshot revision ``tokenizer.json`` resolves to, offline."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except Exception:  # noqa: BLE001
        return None
    p = try_to_load_from_cache(model, "tokenizer.json")
    if not isinstance(p, str):
        return None
    # <hub>/models--org--name/snapshots/<revision>/tokenizer.json (the path itself may
    # contain other "snapshots" components, e.g. /rag/snapshots/...)
    rev_dir = pathlib.Path(p).parent
    return rev_dir.name if rev_dir.parent.name == "snapshots" else None


def main() -> int:
    reg = pathlib.Path(sys.argv[1])
    out_path = pathlib.Path(sys.argv[2])
    manifest = json.loads((reg / "MANIFEST.json").read_text())
    tok = manifest["tokenizer"]

    import ragstack
    from ragstack.ingestion import chunkers
    from ragstack.ingestion.chunkers import FixedTokenWindowChunker, sentence_spans
    from ragstack.ingestion.tokenization import make_token_counter
    from ragstack.models import Document

    try:
        tc = make_token_counter(tok["backend"], model=tok["model"])
    except Exception as e:  # noqa: BLE001 - any load failure offline = unavailable
        print(f"cannot load tokenizer {tok['model']!r} offline: {type(e).__name__}: {e}",
              file=sys.stderr)
        return EXIT_NO_TOKENIZER
    if not callable(getattr(tc, "_tokenizer", None)):
        raise SystemExit(f"token counter backend is not hf: {type(tc).__name__}")

    env: dict = {"ragstack_file": ragstack.__file__,
                 "tokenizer_revision": _tokenizer_revision(tok["model"])}
    for name in ("nltk", "transformers", "tokenizers"):
        try:
            env[name] = getattr(__import__(name), "__version__", "?")
        except ImportError:
            env[name] = None
    env["sentence_backend"] = (
        "punkt" if chunkers._punkt_sentence_spans("A b. C d.") is not None else "regex")

    arms = manifest["arms"]
    built = {a["key"]: FixedTokenWindowChunker(
        chunk_size=a["chunk_size"], chunk_overlap=a["chunk_overlap"], token_counter=tc)
        for a in arms}
    units_map = {}
    for line in (reg / "inputs/units.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        units_map[r["docno"]] = r["units"]

    spans_out: dict[str, dict[str, str]] = {a["key"]: {} for a in arms}
    sent_docs: dict[str, str] = {}
    for line in (reg / "inputs/docs.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        docno, text, title = rec["docno"], rec["text"], rec["title"]
        units = units_map.get(docno, [])
        doc = Document(id=docno, content=text)
        for a in arms:
            chunks = built[a["key"]].chunk(doc)
            spans = [[c.start_char, c.end_char, tc.count(c.content)] for c in chunks]
            v: dict = {"spans": spans}
            if a["header"]:
                hs = []
                for s, _e, _n in spans:
                    sec = ""
                    for u in units:
                        if u["start_char"] <= s < u["end_char"]:
                            sec = u["title"] or u["cls"]
                            break
                    hs.append(f"«{title} — {sec}»\n" if (title or sec) else "")
                v["hdr"] = hs
            spans_out[a["key"]][docno] = json.dumps({"docno": docno, **v})
        sent_docs[docno] = json.dumps(
            {"docno": docno, "spans": [list(x) for x in sentence_spans(text)]})

    canon = json.loads((reg / "inputs/canonical-1.json").read_text())
    sent_canon = [json.dumps({"sample": canon["name"], "i": i,
                              "spans": [list(x) for x in sentence_spans(t)]})
                  for i, t in enumerate(canon["texts"])]

    out_path.write_text(json.dumps({"env": env, "spans": spans_out,
                                    "sentences_docs": sent_docs,
                                    "sentences_canonical": sent_canon}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
