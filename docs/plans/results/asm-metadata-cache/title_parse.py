#!/usr/bin/env python3
"""Job 4 (assessment only): can a local parse of `first_page` recover a usable title?

Deliberately a ~70-line heuristic, NOT a production extractor. The point is to
measure the ceiling and the failure modes, scored against the documents that
DO carry a title in production (set C) so the score is objective.
"""
import json, re, sys, difflib, collections

S = "/tmp/claude-3581/-home-wilke-Development-ragstack/5f5c3e4a-7165-4b98-84ba-6da7bc9b431c/scratchpad"
C = "/rag/data/asm-metadata-cache"

MASTHEAD = [
    re.compile(r"^\s*$"),
    re.compile(r"\d{4}-\d{3}[\dX]/\d"),                      # 0022-538X/99/$04.00
    re.compile(r"copyright\s*(©|\(c\))", re.I),
    re.compile(r"all rights reserved", re.I),
    re.compile(r"^\s*\*?\s*(vol(ume)?\.?|no\.|number)\s", re.I),
    re.compile(r"^\s*vol\.?\s*\d+", re.I),
    re.compile(r"^\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}", re.I),
    re.compile(r"\bp{1,2}\.\s*\d+[-–]\d+", re.I),            # p. 6257-6264
    re.compile(r"downloaded from", re.I),
    re.compile(r"^\s*(doi:|https?://)", re.I),
    re.compile(r"american society for microbiology", re.I),
    re.compile(r"^\s*(crossmark|check for updates)", re.I),
    re.compile(r"^[A-Z][A-Za-z ]+ (OF|FOR) [A-Z]", ),        # JOURNAL OF VIROLOGY,
    re.compile(r"^\s*\|"),                                    # " | Open Peer Review | ..."
    re.compile(r"^\s*(minireview|review|research article|editorial|commentary|erratum|"
               r"letter to the editor|guest commentary|spotlight|announcement|"
               r"observation|short report|perspective|opinion/hypothesis)\s*$", re.I),
]
# a line that means "the title is over"
AUTHORS = [
    re.compile(r"^[A-ZÀ-Þ][A-ZÀ-Þ\.\'\- ]{4,}[,\d\*†‡§]"),   # YU-SHIU CHANG,1 CHING-LEN LIAO,2
    re.compile(r"^[A-Z][a-zà-þ]+ .*,[a-z0-9](,[a-z0-9])*\s*$"),  # Cristina Lazzarini,a,b
    re.compile(r"\b(Department|Departamento|University|Universidade|Institute|Instituto|"
               r"Laboratory|Laboratoire|College|Hospital|Center|Centre|Division|School)\b"),
    re.compile(r"^\s*(abstract|summary)\b", re.I),
    re.compile(r"^\s*AUTHOR AFFILIATIONS", re.I),
    re.compile(r"^\s*(and\s+)?[A-Z][a-z]+ [A-Z]\. [A-Z][a-z]+,?\s*\d*\s*\*?\s*$"),
    re.compile(r"\bet al\b", re.I),
    re.compile(r"^\s*Received \d", re.I),
    re.compile(r"^\s*(Editor|Address correspondence)", re.I),
]


def is_masthead(l):
    return any(p.search(l) for p in MASTHEAD)


def is_author(l):
    return any(p.search(l) for p in AUTHORS)


def extract(first_page):
    if not first_page:
        return None
    lines = [l.rstrip() for l in first_page.split("\n")]
    i = 0
    # 1. skip the masthead block (bounded: the title is near the top)
    while i < len(lines) and i < 25 and is_masthead(lines[i]):
        i += 1
    # 2. take consecutive title lines
    got = []
    while i < len(lines) and len(got) < 6:
        l = lines[i]
        if not l.strip():
            if got:
                break
            i += 1
            continue
        if is_masthead(l):
            if got:
                break
            i += 1
            continue
        if is_author(l):
            break
        got.append(l.strip())
        i += 1
    t = re.sub(r"\s+", " ", " ".join(got)).strip(" .,;:")
    t = re.sub(r"[\*†‡§¶]+$", "", t).strip()
    return t or None


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


JUNKY = re.compile(r"^[a-z]{2,4}[\d\.\-_]{4,}[a-z\d]*$", re.I)


def looks_like_id(t):
    """An ES 'title' that is really a PDF internal id, e.g. 'jv089906257p'."""
    if not t:
        return False
    return bool(JUNKY.match(t.strip())) or (" " not in t.strip() and len(t.strip()) < 30)


def main():
    tg = {json.loads(l)["source_path"]: json.loads(l) for l in open(f"{C}/state/fp-targets.jsonl")}
    ti = json.load(open(f"{S}/sample-titles.json"))
    hits = [json.loads(l) for l in open(f"{S}/fp-hits.jsonl")]

    res = collections.defaultdict(lambda: collections.Counter())
    examples = collections.defaultdict(list)
    out = open(f"{S}/title-parse-eval.jsonl", "w")
    for h in hits:
        t = tg[h["source_path"]]
        st = t["_set"]
        gold = ti.get(t["doc_id"])
        pred = extract(h["first_page"])
        row = {"doc_id": t["doc_id"], "set": st, "doc_type": t["doc_type"],
               "filename": t["filename"], "gold": gold, "pred": pred,
               "gold_is_id": looks_like_id(gold)}
        res[st]["n"] += 1
        if not h["first_page"]:
            res[st]["no_first_page"] += 1
        if pred:
            res[st]["produced"] += 1
            wc = len(pred.split())
            if 4 <= wc <= 45:
                res[st]["plausible_len"] += 1
            else:
                res[st]["implausible_len"] += 1
                examples["implausible_" + st].append(row)
        else:
            res[st]["no_output"] += 1
            examples["no_output_" + st].append(row)
        if gold:
            if looks_like_id(gold):
                res[st]["gold_is_internal_id"] += 1
            else:
                r = difflib.SequenceMatcher(None, norm(gold), norm(pred or "")).ratio()
                row["ratio"] = round(r, 3)
                res[st]["scored"] += 1
                if r >= 0.90:
                    res[st]["exact_ish"] += 1
                elif r >= 0.60:
                    res[st]["partial"] += 1
                    examples["partial_" + st].append(row)
                else:
                    res[st]["wrong"] += 1
                    examples["wrong_" + st].append(row)
        out.write(json.dumps(row, ensure_ascii=False) + "\n")
    out.close()

    for st in sorted(res):
        print(f"\n== {st} ==")
        c = res[st]
        for k in ["n", "no_first_page", "produced", "no_output", "plausible_len",
                  "implausible_len", "gold_is_internal_id", "scored", "exact_ish",
                  "partial", "wrong"]:
            if c[k]:
                pct = f" ({c[k]/c['n']:.1%})" if k != "n" else ""
                print(f"  {k:22s} {c[k]:5d}{pct}")
        if c["scored"]:
            print(f"  -> title recovered (>=0.90 on scored) : {c['exact_ish']}/{c['scored']}"
                  f" = {c['exact_ish']/c['scored']:.1%}")
            print(f"  -> recovered or near (>=0.60)         : "
                  f"{(c['exact_ish']+c['partial'])/c['scored']:.1%}")

    print("\n\n===== FAILURE EXAMPLES =====")
    for k in sorted(examples):
        if not examples[k]:
            continue
        print(f"\n--- {k} (n={len(examples[k])}) ---")
        for r in examples[k][:4]:
            print(f"  file={r['filename']}")
            print(f"    gold={str(r['gold'])[:110]}")
            print(f"    pred={str(r['pred'])[:110]}")


if __name__ == "__main__":
    main()
