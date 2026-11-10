"""Snapshot of what ``alias_study_run.py`` writes in its existing modes (dry run, pilot, main,
rechunk stage 1), for the byte-identity test of the reach run's opt-in mode
(``test_reach_run_mode.py``). Run as a script in a subprocess:

    python run_mode_snapshot.py WORKDIR OUT.json [OLD_MODULE_DIR]

``OLD_MODULE_DIR``: a directory holding another version of ``alias_study_run.py`` and
``rechunk_prep.py`` (e.g. from ``git show <commit>:…``); they are imported first, so every later
``import alias_study_run`` / ``import rechunk_prep`` (the test helpers', the runner's own) gets
them. The snapshot is every file the runs write, with the fields that differ between two runs of
the same code (wall-clock times, latencies, the provenance block) removed. A fake API and a fake
census index; no store, no network."""
from __future__ import annotations

import hashlib
import importlib
import json
import sys
from pathlib import Path
from typing import Any

#: per-run values (times, latencies, the checkout's provenance), never the behaviour under test; and
#: the shas of the fixture's glossary files and load receipts, which the fixture writes with a
#: timestamp (two runs of the same code differ there and nowhere else)
VOLATILE = frozenset({"utc", "latency_ms", "provenance", "t", "elapsed_s", "started_utc", "finished_utc",
                      "duration_s", "wall_s", "glossary_file_sha256", "load_receipt_sha256"})


def scrub(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items() if k not in VOLATILE}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    return obj


def snapshot_dir(d: Path, root: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for p in sorted(d.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root))
        text = p.read_text(encoding="utf-8").replace(str(root), "<root>")
        if p.suffix == ".json":
            out[rel] = scrub(json.loads(text))
        elif p.suffix == ".jsonl":
            out[rel] = [scrub(json.loads(x)) for x in text.splitlines() if x.strip()]
        else:
            out[rel] = text
    return out


def main(argv: list[str]) -> int:
    work, out = Path(argv[0]), Path(argv[1])
    here = Path(__file__).resolve().parent
    eval_dir = here.parents[1] / "scripts" / "eval"
    for p in (here, eval_dir, eval_dir / "glossbench"):
        if str(p) not in sys.path:
            sys.path.append(str(p))
    if len(argv) > 2:
        sys.path.insert(0, argv[2])
        for name in ("rechunk_prep", "alias_study_run"):
            importlib.import_module(name)
        sys.path.remove(argv[2])
    import alias_study_common as asc
    import alias_study_run as ar
    import test_alias_study_run as t

    if len(argv) > 2:
        assert Path(ar.__file__).parent == Path(argv[2]), "the old runner was not the one imported"
    asc.verify_record_set = lambda path, recs: {"accepted_sha256": "x", "records": len(recs)}  # type: ignore[assignment]

    class _MP:
        """The bit of pytest's monkeypatch ``rechunk_setup`` uses."""

        @staticmethod
        def setattr(obj: Any, name: str, value: Any) -> None:
            setattr(obj, name, value)

    snaps: dict[str, Any] = {}
    # 1. the dry run, base x none (stage A) and base x merged
    d = work / "dry"
    d.mkdir(parents=True)
    w, cfg = t.setup(d)
    rcs = [t.run(cfg, t.API(w)), t.run(cfg, t.API(w), glossary="merged")]
    snaps["dry"] = {"rc": rcs, "files": snapshot_dir(Path(cfg["out_dir"]), d)}
    # 2. the pilot (record set verified; only the descriptive records)
    d = work / "pilot"
    d.mkdir(parents=True)
    w, cfg = t.setup(d, dry_run=False, pilot=True, census={"es_url": t.ES})
    rcs = [t.run(cfg, t.API(w), census=t.Census())]
    snaps["pilot"] = {"rc": rcs, "files": snapshot_dir(Path(cfg["out_dir"]), d)}
    # 3. the main run (a frozen prereg pinned)
    d = work / "main"
    d.mkdir(parents=True)
    pre = d / "PREREG.md"
    pre.write_text("frozen\n")
    w, cfg = t.setup(d, dry_run=False, census={"es_url": t.ES},
                     prereg={"file": str(pre), "sha256": hashlib.sha256(b"frozen\n").hexdigest()})
    rcs = [t.run(cfg, t.API(w), census=t.Census()), t.run(cfg, t.API(w), corpus="neighbour", census=t.Census())]
    snaps["main"] = {"rc": rcs, "files": snapshot_dir(Path(cfg["out_dir"]), d)}
    # 4. rechunk stage 1 (a label table)
    d = work / "rechunk"
    d.mkdir(parents=True)
    w, cfg = t.rechunk_setup(d, _MP())  # type: ignore[arg-type]
    rcs = [t.run(cfg, t.API(w), corpus=c, census=t.Census()) for c in ("base", "neighbour", "random1")]
    snaps["rechunk"] = {"rc": rcs, "files": snapshot_dir(Path(cfg["out_dir"]), d)}
    out.write_text(json.dumps(snaps, indent=1, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
