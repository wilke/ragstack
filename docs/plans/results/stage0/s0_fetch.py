"""Stage 0a step 2 -- fetch the corpus XML (SS4.2, S1's measured 98.5% path).

Verbatim in behaviour from ``step2/fetch.py``: pmc-oa-opendata S3, versions 1..3, 32
threads. Already-fetched articles are hard-linked/copied from the Phase-0 dirs first, so a
document that step 2 or the SS7a oracle already pulled is byte-identical here.
"""
from __future__ import annotations

import collections
import concurrent.futures as cf
import shutil
import urllib.error
import urllib.request

import s0_common as C

REUSE = [C.STEP2 / "xml", C.PILOTS / "xml", C.PHASE0 / "review" / "xml200",
         C.CDS / "xml"]


def main() -> None:
    ids = [x.strip() for x in (C.WORK / "fetchlist.txt").read_text().split() if x.strip()]
    reused = 0
    for pmcid in ids:
        dst = C.XML / f"PMC{pmcid}.xml"
        if dst.exists() and dst.stat().st_size > 0:
            continue
        for d in REUSE:
            src = d / f"PMC{pmcid}.xml"
            if src.exists() and src.stat().st_size > 0:
                shutil.copyfile(src, dst)
                reused += 1
                break
    todo = [i for i in ids if not (C.XML / f"PMC{i}.xml").exists()]
    print(f"want {len(ids)}  reused {reused}  to fetch {len(todo)}", flush=True)

    def fetch(pid):
        for ver in (1, 2, 3):
            url = (f"https://pmc-oa-opendata.s3.amazonaws.com/PMC{pid}.{ver}/"
                   f"PMC{pid}.{ver}.xml")
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    data = r.read()
                (C.XML / f"PMC{pid}.xml").write_bytes(data)
                return (pid, f"OK v{ver}")
            except urllib.error.HTTPError as e:
                if e.code in (403, 404):
                    continue
                return (pid, f"HTTP{e.code}")
            except Exception as e:  # noqa: BLE001
                return (pid, f"ERR {type(e).__name__}")
        return (pid, "MISS")

    res = []
    with cf.ThreadPoolExecutor(32) as ex:
        for i, r in enumerate(ex.map(fetch, todo)):
            res.append(r)
            if (i + 1) % 2000 == 0:
                print("  ...", i + 1, flush=True)
    print(collections.Counter(s.split()[0] for _, s in res), flush=True)
    miss = [p for p, s in res if not s.startswith("OK")]
    (C.WORK / "fetch_misses.txt").write_text("\n".join(miss) + "\n")
    with open(C.WORK / "fetch_log.txt", "w") as f:
        for p, s in res:
            f.write(f"{p} {s}\n")
    present = len(list(C.XML.glob("*.xml")))
    C.atomic_json(C.WORK / "fetch_stats.json", {
        "wanted": len(ids), "reused": reused, "attempted": len(todo),
        "misses": len(miss), "present": present,
        "fetch_rate": round(present / max(len(ids), 1), 4)})
    print("misses:", len(miss), "present:", present, flush=True)


if __name__ == "__main__":
    main()
