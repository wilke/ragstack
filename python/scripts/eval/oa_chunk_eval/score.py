"""``score``: per question x arm x k, primary coverage hit, strict hit, context tokens.

Primary hit@k (Addendum A.2): the union of the top-k chunks whose ``source_path`` equals
the gold record path covers >= ``score.coverage`` (0.90) of the gold quote's characters.
A chunk's position in the record text comes from its ``start_char``/``end_char`` when
present. With ``score.offsets: "verify"`` (default) the offsets are used only when the
record text at those offsets equals the chunk content after whitespace normalisation;
otherwise, and when offsets are absent, the chunk content is located in the record text
(exact, then whitespace-normalised). If the content occurs more than once, the occurrence
overlapping the quote most is used (the text is identical, so the reader saw the same
words). A chunk that cannot be located contributes no coverage and is counted:
``unlocated``, or ``offsets_unverified`` when it carried offsets that failed verification
(those offsets are NOT used). With ``"trust"`` offsets are used unverified; ``"ignore"``
always searches.

Repeated quotes: when the whitespace-normalised quote occurs more than once in the gold
record, the primary coverage is the MAXIMUM over all occurrences (each occurrence is the
same words, so a chunk holding any of them answers the question). This keeps primary
consistent with strict, which matches by text. ``audit`` also flags such items
``within_record_repeat`` and they are scored with the ambiguous set.

Strict hit@k (secondary): one top-k chunk of the gold record contains the whole quote
(whitespace-normalised substring).

Context tokens per query: the sum of the top-k chunk lengths. ``score.tokenizer`` names the
tokenizer: a string is an HF model id for ``ragstack.ingestion.tokenization.make_token_counter``
(backend ``hf``); a dict is passed to it as keyword arguments. Absent, or failing to load,
gives len/4 and ``tokens_estimated: true`` in every output. The tokenizer name written to
the outputs is ``score.tokenizer_label`` when set, else only the basename of the model
(never a local path).

Question sets: ``clean`` (neither ``ambiguous_gold`` nor ``within_record_repeat`` per
``audit.jsonl``; the primary set), ``ambiguous`` and ``all``. Only questions with a usable
``ok`` retrieval row in EVERY arm are scored (paired); the per-arm count of missing rows is
reported. Questions whose gold record text is not in the extracted JSONL are dropped from
the paired set and counted (``records_without_text``).

Request check (:func:`retrieve.usable_ok`): an ok row is used only when its ``request``
equals the request the current config would send to that arm. A question whose ok rows
were ALL fetched with a different request is missing for that arm AND a refusal
(``request_mismatch_by_arm``). Rows without a ``request`` (older ledgers) are used and
counted per arm in ``request_unverified_by_arm`` with a warning. ``server_versions``
records per arm the distinct server versions (``version+git_sha``) of the rows actually
scored; mixed versions within an arm or across the run are logged as a warning only.

Noise-subset exclusion (``score.exclude_noise_subset``, default false; owner 2026-11-05):
when true, the questions of the noise subset, selected by ``noise.noise_questions`` (the
same function, config and seed ``noise`` uses; not re-implemented here), are flagged
``noise_subset: true`` in ``per_question.jsonl`` and left out of the primary set, which is
then named ``clean_excl_noise`` (``all_excl_noise`` without an audit). Ambiguous items are
handled as before. The set WITH the noise subset (``clean`` / ``all``) is still scored in
full and named in ``sensitivity_set`` with ``sensitivity_label``: a sensitivity result, NOT
the decision set. ``noise_exclusion`` in ``score.json`` records the subset size, how many
questions it removed from the primary set, both set sizes and ``excluded_ids_sha256``
(:func:`noise.ids_sha256` of the excluded ids; ``decide`` checks it against ``noise.json``). :func:`set_rows` is the one
definition of every question set, used by ``score`` and ``decide``.

Quality refusals (``refusals`` in ``score.json``; ``score`` exits 2 and ``decide`` refuses):
any ``records_without_text``; per arm, questions whose only ok rows mismatch the current
request (see above); per arm, a share of hits with an empty ``source_path`` above
``score.max_unlocated_share`` (0.01), or a share of gold-record hits that could not be
located (``unlocated`` + ``offsets_unverified``) above the same threshold.

Skip loss by stratum counts, per stratum value: passages, passages with no passing
question (``skips.jsonl``), passages still pending (``pending.jsonl``) and eval articles
dropped before question generation (``passages.skipped.jsonl``, one unit each). The loss
share is (skipped + pending + passage_stage) / (passages + passage_stage).

Check-writer set (``questions.check.jsonl``, audited by ``audit.check.jsonl``): scored the
same way and reported as a separate secondary block ``check_writer``; it never feeds the
decision.

Outputs (private ``out_dir``): ``per_question.jsonl`` and ``score.json`` (aggregates only:
per-arm hit@k with CIs, paired differences vs the baseline with paired bootstrap CIs,
pooled and per stratum, discordance per arm pair, journal confusion, skip loss by stratum
and the pooled differences without the strata that lose more than 10% of passages).
"""
from __future__ import annotations

import itertools
from collections import Counter, defaultdict
from pathlib import PurePath
from typing import Any

from .common import article_of, log, norm_ws, norm_ws_map, read_jsonl, write_json, write_jsonl
from .config import arms as cfg_arms
from .config import baseline_arm, out_path, require
from .noise import ids_sha256, noise_questions
from .passages import iter_records
from .retrieve import usable_ok
from .stats import discordance, mean_ci, paired_diff, strip_private

# ------------------------------------------------------------------- locating chunks


class RecordText:
    """A record text with a lazily built whitespace-normalised view."""

    def __init__(self, text: str) -> None:
        self.text = text
        self._norm: tuple[str, list[int]] | None = None

    @property
    def norm(self) -> tuple[str, list[int]]:
        if self._norm is None:
            self._norm = norm_ws_map(self.text)
        return self._norm


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def _all_find(hay: str, needle: str) -> list[int]:
    out, i = [], hay.find(needle)
    while i >= 0:
        out.append(i)
        i = hay.find(needle, i + 1)
    return out


def locate_chunk(rec: RecordText, content: str, start: int | None, end: int | None,
                 quote: tuple[int, int], offsets: str = "verify") -> tuple[tuple[int, int] | None, str]:
    """``(interval in rec.text, how)``; how is offsets|exact|normalised|offsets_unverified|unlocated."""
    has_off = start is not None and end is not None and 0 <= start < end <= len(rec.text)
    if has_off and offsets == "trust":
        return (start, end), "offsets"
    if has_off and offsets == "verify" and norm_ws(rec.text[start:end]) == norm_ws(content):
        return (start, end), "offsets"
    if content:
        hits = _all_find(rec.text, content)
        if hits:
            best = max(hits, key=lambda i: (_overlap((i, i + len(content)), quote), -i))
            return (best, best + len(content)), "exact"
        nc = norm_ws(content)
        ntext, idx = rec.norm
        if nc:
            hits = _all_find(ntext, nc)
            if hits:
                spans = [(idx[i], idx[i + len(nc) - 1] + 1) for i in hits]
                return max(spans, key=lambda s: (_overlap(s, quote), -s[0])), "normalised"
    if has_off and offsets == "verify":
        # the offsets failed verification and the content was not found: no coverage
        return None, "offsets_unverified"
    return None, "unlocated"


def quote_occurrences(rec: RecordText, nquote: str, quote: tuple[int, int]) -> list[tuple[int, int]]:
    """Every occurrence of the whitespace-normalised quote in ``rec.text`` as raw spans,
    the recorded ``quote`` span first (always included)."""
    out = [quote]
    if nquote:
        ntext, idx = rec.norm
        for i in _all_find(ntext, nquote):
            span = (idx[i], idx[i + len(nquote) - 1] + 1)
            if span not in out:
                out.append(span)
    return out


def coverage(intervals: list[tuple[int, int]], quote: tuple[int, int]) -> float:
    """Share of the quote's characters covered by the union of ``intervals``."""
    qs, qe = quote
    if qe <= qs:
        return 0.0
    clipped = sorted((max(a, qs), min(b, qe)) for a, b in intervals if min(b, qe) > max(a, qs))
    covered, cur_s, cur_e = 0, None, None
    for a, b in clipped:
        if cur_e is None or a > cur_e:
            if cur_e is not None:
                covered += cur_e - cur_s
            cur_s, cur_e = a, b
        else:
            cur_e = max(cur_e, b)
    if cur_e is not None:
        covered += cur_e - cur_s
    return covered / (qe - qs)


def score_question(q: dict[str, Any], hits: list[dict[str, Any]], rec: RecordText | None, ks: list[int],
                   threshold: float, offsets: str, tok) -> dict[str, Any]:
    """Per-k results for one question x arm. ``tok(hit) -> int`` counts a chunk's tokens."""
    quote = (int(q["quote_start"]), int(q["quote_end"]))
    nquote = norm_ws(q["answer_quote"])
    gold = q["record_path"]
    occs = quote_occurrences(rec, nquote, quote) if rec is not None else [quote]

    def locate_all(occ: tuple[int, int]) -> tuple[list[tuple[int, int] | None], list[str]]:
        located: list[tuple[int, int] | None] = []
        hows: list[str] = []
        for h in hits:
            if not h.get("source_path"):
                iv, how = None, "empty_source_path"
            elif h.get("source_path") == gold and rec is not None:
                iv, how = locate_chunk(rec, h.get("content") or "", h.get("start_char"), h.get("end_char"),
                                       occ, offsets)
            else:
                iv, how = None, "other_record"
            located.append(iv)
            hows.append(how)
        return located, hows

    per_occ = [locate_all(o) for o in occs]
    hows = per_occ[0][1]  # the diagnostics come from the recorded occurrence
    out: dict[str, Any] = {}
    for k in ks:
        top = hits[:k]
        cov = max(coverage([iv for iv in located[:k] if iv is not None], occ)
                  for occ, (located, _) in zip(occs, per_occ, strict=True))
        strict = any(h.get("source_path") == gold and nquote in norm_ws(h.get("content") or "") for h in top)
        out[str(k)] = {"primary": int(cov >= threshold), "coverage": round(cov, 4), "strict": int(strict),
                       "ctx_tokens": int(sum(tok(h) for h in top))}
    top1 = hits[0] if hits else None
    out["top1_article"] = article_of(top1["source_path"]) if top1 else None
    out["locate"] = {h: hows.count(h) for h in set(hows)}
    out["quote_occurrences"] = len(occs)
    return out


# ------------------------------------------------------------------- tokenizer


def tokenizer_label(spec: Any, label: str | None = None) -> str:
    """The name written to score/decision outputs: the explicit ``label``, else the basename
    of the model (a local model path must not reach committed files), else the backend."""
    if label:
        return str(label)
    kw = {"model": spec} if isinstance(spec, str) else dict(spec or {})
    model = kw.get("model")
    if model:
        return PurePath(str(model).rstrip("/\\")).name or "tokenizer"
    return str(kw.get("backend") or "tokenizer")


def make_tokenizer(spec: Any, label: str | None = None):
    """``(count(text) -> int, name, estimated)``."""
    if spec:
        try:
            from ragstack.ingestion.tokenization import make_token_counter
            kw = {"backend": "hf", "model": spec} if isinstance(spec, str) else dict(spec)
            counter = make_token_counter(**kw)
            return counter.count, tokenizer_label(spec, label), False
        except Exception as e:  # noqa: BLE001 - any load failure falls back, flagged
            log(f"score: tokenizer {tokenizer_label(spec, label)!r} unavailable ({type(e).__name__}); "
                "using chars/4, flagged")
    return (lambda text: (len(text) + 3) // 4), "chars/4", True



# ------------------------------------------------------------------- aggregation


def audit_flags(cfg: dict[str, Any], audit_file: str = "audit.jsonl") -> set[str] | None:
    """Question ids scored with the ambiguous set: ``ambiguous_gold`` (the quote is in
    another record) or ``within_record_repeat`` (it occurs more than once in the gold
    record). None when the audit has not run."""
    p = out_path(cfg, audit_file)
    if not p.exists():
        return None
    return {r["question_id"] for r in read_jsonl(p)
            if r.get("ambiguous_gold") or r.get("within_record_repeat")}


#: kept for callers that used the old name
ambiguous_ids = audit_flags

_PASSAGE_STRATA = ("journal_family", "length_band", "year_band")


def skip_loss(cfg: dict[str, Any], dims: list[str], suffix: str = "") -> dict[str, dict[str, Any]]:
    """Per dim/value: passages, skipped (no passing question), pending (calls never
    completed), passage_stage (eval articles with no passage), and the loss share."""
    passages = read_jsonl(out_path(cfg, "passages.jsonl"))
    skips = {r["passage_id"] for r in read_jsonl(out_path(cfg, f"skips{suffix}.jsonl"))}
    pending = {r["passage_id"] for r in read_jsonl(out_path(cfg, f"pending{suffix}.jsonl"))}
    stage = read_jsonl(out_path(cfg, "passages.skipped.jsonl")) if not suffix else []
    if suffix:  # the check writer runs on a subset of passages: only count those it saw
        seen = {r["passage_id"] for r in read_jsonl(out_path(cfg, f"questions{suffix}.jsonl"))} | skips | pending
        passages = [p for p in passages if p["passage_id"] in seen]
    out: dict[str, dict[str, Any]] = {}
    for d in dims:
        tot: dict[str, int] = defaultdict(int)
        sk: dict[str, int] = defaultdict(int)
        pe: dict[str, int] = defaultdict(int)
        ps: dict[str, int] = defaultdict(int)
        for p in passages:
            v = str(p.get(d))
            tot[v] += 1
            sk[v] += p["passage_id"] in skips
            pe[v] += p["passage_id"] in pending
        for s in stage:
            # section_type is unknown before a passage exists
            v = str(s.get(d)) if d in _PASSAGE_STRATA else "None"
            ps[v] += 1
        out[d] = {}
        for v in sorted(set(tot) | set(ps)):
            denom = tot[v] + ps[v]
            lost = sk[v] + pe[v] + ps[v]
            out[d][v] = {"passages": tot[v], "skipped": sk[v], "pending": pe[v], "passage_stage": ps[v],
                         "loss": round(lost / denom, 4) if denom else 0.0}
    return out


def lossy_strata(loss: dict[str, dict[str, Any]], threshold: float) -> dict[str, list[str]]:
    return {d: [v for v, x in vals.items() if x["loss"] > threshold] for d, vals in loss.items()}


def vectors(rows: list[dict[str, Any]], arm_names: list[str], k: int, metric: str) -> dict[str, list[int]]:
    """arm -> per-question 0/1 vector, questions in ``rows`` order (rows are per question)."""
    return {a: [r["arms"][a][str(k)][metric] for r in rows] for a in arm_names}


def compare(rows: list[dict[str, Any]], arm_names: list[str], base: str, k: int, metric: str,
            iters: int, seed: int) -> dict[str, Any]:
    vec = vectors(rows, arm_names, k, metric)
    out: dict[str, Any] = {"n": len(rows), "hit": {}, "diff": {}}
    for a in arm_names:
        out["hit"][a] = strip_private(mean_ci(vec[a], iters, seed))
        if a != base:
            out["diff"][a] = strip_private(paired_diff(vec[a], vec[base], iters, seed))
    return out


def by_stratum(rows: list[dict[str, Any]], dims: list[str], fn) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for d in dims:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            groups[str(r["strata"].get(d))].append(r)
        out[d] = {v: fn(g) for v, g in sorted(groups.items())}
    return out


def journal_confusion(rows: list[dict[str, Any]], arm_names: list[str], fam_of: dict[str, str]) -> dict[str, Any]:
    out = {}
    fam_counts: dict[str, int] = defaultdict(int)
    for f in fam_of.values():
        fam_counts[f] += 1
    total = sum(fam_counts.values()) or 1
    for a in arm_names:
        wrong = same = unknown = 0
        chance = 0.0
        for r in rows:
            t1 = r["arms"][a]["top1_article"]
            gold = r["pmcid"]
            if t1 is None or t1 == gold:
                continue
            wrong += 1
            gf = r["strata"].get("journal_family")
            tf = fam_of.get(t1)
            if tf is None:  # unknown family: in neither share (same denominator, ``known``)
                unknown += 1
                continue
            if tf == gf:
                same += 1
            chance += (fam_counts.get(gf, 0) - 1) / max(1, total - 1)
        known = wrong - unknown
        out[a] = {"wrong_top1": wrong, "same_family": same, "unknown_family": unknown,
                  "share_same_family": round(same / known, 4) if known else None,
                  "chance_share": round(chance / known, 4) if known else None}
    return out


def hit_quality(rows: list[dict[str, Any]], arm_names: list[str]) -> dict[str, dict[str, Any]]:
    """Per arm: share of all hits with an empty ``source_path``; counts and share of
    gold-record hits that were ``unlocated`` / ``offsets_unverified``."""
    out: dict[str, dict[str, Any]] = {}
    for a in arm_names:
        c: Counter = Counter()
        for r in rows:
            c.update(r["arms"][a]["locate"])
        total = sum(c.values())
        gold_hits = sum(v for k, v in c.items() if k not in ("other_record", "empty_source_path"))
        bad = c["unlocated"] + c["offsets_unverified"]
        out[a] = {"hits": total, "empty_source_path": c["empty_source_path"],
                  "empty_source_path_share": round(c["empty_source_path"] / total, 6) if total else 0.0,
                  "gold_record_hits": gold_hits, "unlocated": c["unlocated"],
                  "offsets_unverified": c["offsets_unverified"],
                  "unlocated_share": round(bad / gold_hits, 6) if gold_hits else 0.0}
    return out


def refusals(meta: dict[str, Any], quality: dict[str, dict[str, Any]], max_share: float) -> list[str]:
    """Reasons the scores must not be used for a decision (empty: fine)."""
    out = []
    if meta.get("records_without_text"):
        out.append(f"records_without_text={meta['records_without_text']} (gold record text missing from the "
                   "extracted JSONL; those questions were dropped)")
    for a, n in (meta.get("request_mismatch_by_arm") or {}).items():
        if n:
            out.append(f"{a}: {n} question(s) have only ok retrieval rows fetched with a different request than "
                       "the current config sends (rerun retrieve, or score with the config they were fetched with)")
    for a, q in quality.items():
        if q["empty_source_path_share"] > max_share:
            out.append(f"{a}: empty_source_path_share={q['empty_source_path_share']} > {max_share}")
        if q["unlocated_share"] > max_share:
            out.append(f"{a}: unlocated_share={q['unlocated_share']} > {max_share} "
                       f"(unlocated {q['unlocated']}, offsets_unverified {q['offsets_unverified']})")
    return out


def version_label(sv: Any) -> str:
    """One string per server identity of a row: ``version+git_sha``, ``error:<why>`` when
    ``/v1/version`` failed, ``unknown`` when the row carries none."""
    if not isinstance(sv, dict):
        return "unknown"
    if sv.get("error"):
        return f"error:{sv['error']}"
    v, sha = sv.get("version"), sv.get("git_sha")
    if v is None and sha is None:
        return "unknown"
    return f"{v}+{sha}" if sha else str(v)


def warn_request_and_versions(meta: dict[str, Any]) -> None:
    """Log (never refuse): rows without a recorded request, and mixed server versions."""
    unv = {a: n for a, n in (meta.get("request_unverified_by_arm") or {}).items() if n}
    if unv:
        log("score: WARNING: rows without a recorded request (unverifiable, used): "
            + ", ".join(f"{a} {n}" for a, n in unv.items()))
    versions = meta.get("server_versions") or {}
    for a, vs in versions.items():
        if len(vs) > 1:
            log(f"score: WARNING: arm {a} mixes server versions: {', '.join(vs)}")
    run_vs = sorted({v for vs in versions.values() for v in vs})
    if len(run_vs) > 1:
        log(f"score: WARNING: the run mixes server versions: {', '.join(run_vs)}")


def build_rows(cfg: dict[str, Any], arm_names: list[str] | None = None,
               questions: list[dict[str, Any]] | None = None,
               audit_file: str = "audit.jsonl") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Per-question rows with per-arm results for questions complete in every arm whose
    gold record text is available."""
    arm_names = arm_names or [a["name"] for a in cfg_arms(cfg)]
    sc = cfg["score"]
    ks = [int(k) for k in sc["ks"]]
    if questions is None:
        questions = read_jsonl(out_path(cfg, "questions.jsonl"))
    amb = audit_flags(cfg, audit_file)
    ret: dict[str, dict[str, dict[str, Any]]] = {}
    req_stats: dict[str, dict[str, Any]] = {}
    for a in arm_names:
        ret[a], req_stats[a] = usable_ok(cfg, a)
    qids = {q["question_id"] for q in questions}
    mismatch = {a: len(req_stats[a]["mismatch_only"] & qids) for a in arm_names}
    missing = {a: sum(1 for q in questions if q["question_id"] not in ret[a]) for a in arm_names}
    complete = [q for q in questions if all(q["question_id"] in ret[a] for a in arm_names)]
    gold_paths = {q["record_path"] for q in complete}
    texts: dict[str, RecordText] = {}
    for rec in iter_records(cfg):
        if rec["path"] in gold_paths and rec["path"] not in texts:
            texts[rec["path"]] = RecordText(rec["text"])
    no_text = [q for q in complete if q["record_path"] not in texts]
    complete = [q for q in complete if q["record_path"] in texts]
    used = {q["question_id"] for q in complete}
    unverified = {a: len(req_stats[a]["unverified"] & used) for a in arm_names}
    versions = {a: sorted({version_label(ret[a][qid].get("server_version")) for qid in used}) for a in arm_names}
    count, tok_name, estimated = make_tokenizer(sc.get("tokenizer"), sc.get("tokenizer_label"))
    tok_cache: dict[tuple[str, str], int] = {}

    def tok_for(arm: str):
        def tok(h: dict[str, Any]) -> int:
            key = (arm, str(h.get("chunk_id")))
            if key not in tok_cache:
                tok_cache[key] = int(count(h.get("content") or ""))
            return tok_cache[key]
        return tok

    rows = []
    for q in complete:
        r = {"question_id": q["question_id"], "pmcid": q["pmcid"],
             "ambiguous_gold": None if amb is None else q["question_id"] in amb,
             "strata": {d: q.get(d) for d in sc["report_dims"]}, "arms": {}}
        for a in arm_names:
            r["arms"][a] = score_question(q, ret[a][q["question_id"]].get("hits") or [], texts[q["record_path"]],
                                          ks, float(sc["coverage"]), sc.get("offsets") or "verify", tok_for(a))
        rows.append(r)
    meta = {"n_questions": len(questions), "n_complete": len(complete), "missing_by_arm": missing,
            "tokenizer": tok_name, "tokens_estimated": estimated, "audit_present": amb is not None,
            "records_without_text": len(gold_paths - set(texts)), "questions_without_record_text": len(no_text),
            "request_mismatch_by_arm": mismatch, "request_unverified_by_arm": unverified,
            "server_versions": versions}
    warn_request_and_versions(meta)
    quality = hit_quality(rows, arm_names)
    meta["hit_quality"] = quality
    meta["refusals"] = refusals(meta, quality, float(sc.get("max_unlocated_share", 0.01)))
    return rows, meta


def _set_block(rs: list[dict[str, Any]], arm_names: list[str], base: str, ks: list[int], dims: list[str],
               iters: int, seed: int, lossy: dict[str, list[str]] | None) -> dict[str, Any]:
    block: dict[str, Any] = {"n": len(rs), "primary": {}, "strict": {}, "context_tokens": {},
                             "discordance": {}}
    for k in ks:
        for metric in ("primary", "strict"):
            block[metric][str(k)] = {
                "pooled": compare(rs, arm_names, base, k, metric, iters, seed),
                "strata": by_stratum(rs, dims, lambda g, k=k, metric=metric: compare(g, arm_names, base, k, metric,
                                                                                    iters, seed)),
            }
        block["context_tokens"][str(k)] = {
            a: (round(sum(r["arms"][a][str(k)]["ctx_tokens"] for r in rs) / len(rs), 2) if rs else None)
            for a in arm_names}
        vec = vectors(rs, arm_names, k, "primary")
        block["discordance"][str(k)] = {f"{a}|{b}": discordance(vec[a], vec[b])
                                       for a, b in itertools.combinations(arm_names, 2)}
    if lossy and any(lossy.values()):
        kept = [r for r in rs if not any(str(r["strata"].get(d)) in v for d, v in lossy.items())]
        block["without_lossy_strata"] = {
            "n": len(kept),
            "primary": {str(k): compare(kept, arm_names, base, k, "primary", iters, seed) for k in ks}}
    block["locate"] = {a: dict(sum((Counter(r["arms"][a]["locate"]) for r in rs), Counter()))
                       for a in arm_names}
    return block


EXCL_NOISE = "_excl_noise"
SENSITIVITY_LABEL = "SENSITIVITY: the question set with the noise subset included; NOT the decision set"


def set_rows(rows: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    """The rows of question set ``name`` (``all``, ``clean``, ``ambiguous``, optionally with
    the ``_excl_noise`` suffix), in ``rows`` order. The single definition used by ``score``
    and ``decide``, so both see the same paired set in the same order (and therefore draw
    the same seeded bootstrap)."""
    excl = name.endswith(EXCL_NOISE)
    base = name[:-len(EXCL_NOISE)] if excl else name
    if base == "all":
        rs = list(rows)
    elif base == "clean":
        rs = [r for r in rows if not r["ambiguous_gold"]]
    elif base == "ambiguous":
        rs = [r for r in rows if r["ambiguous_gold"]]
    else:
        raise ValueError(f"unknown question set {name!r}")
    if excl:
        if rows and not all("noise_subset" in r for r in rows):
            raise ValueError(f"set {name!r} needs per-question noise_subset flags (rerun score)")
        rs = [r for r in rs if not r["noise_subset"]]
    return rs


def mark_noise_subset(cfg: dict[str, Any], rows: list[dict[str, Any]]) -> set[str]:
    """Flag ``noise_subset`` on every row with the ids ``noise.noise_questions`` selects from
    ``questions.jsonl`` (the noise run's own selection, reused). Returns the selected ids."""
    ids = {q["question_id"] for q in noise_questions(cfg, read_jsonl(out_path(cfg, "questions.jsonl")))}
    for r in rows:
        r["noise_subset"] = r["question_id"] in ids
    return ids


def summarise(rows: list[dict[str, Any]], cfg: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    arm_list = cfg_arms(cfg)
    arm_names = [a["name"] for a in arm_list]
    base = baseline_arm(cfg)["name"]
    sc = cfg["score"]
    ks = [int(k) for k in sc["ks"]]
    dims = list(sc["report_dims"])
    iters, seed = int(sc["bootstrap_iters"]), int(sc["bootstrap_seed"])
    names = ["all"] + (["clean", "ambiguous"] if meta.get("audit_present") else [])
    reference = "clean" if meta.get("audit_present") else "all"  # the primary set without the exclusion
    noise_info: dict[str, Any] = {"enabled": False}
    if sc.get("exclude_noise_subset"):
        ids = mark_noise_subset(cfg, rows)
        names.append(reference + EXCL_NOISE)
        ref_rows = set_rows(rows, reference)
        n_excl = sum(1 for r in ref_rows if r["noise_subset"])
        noise_info = {"enabled": True, "n_noise_subset": len(ids), "n_excluded_from_primary": n_excl,
                      "n_primary": len(ref_rows) - n_excl, "n_sensitivity": len(ref_rows),
                      "noise_seed": cfg["noise"]["seed"], "noise_n_questions": int(cfg["noise"]["n_questions"]),
                      "excluded_ids_sha256": ids_sha256(ids)}
    sets = {n: set_rows(rows, n) for n in names}
    loss = skip_loss(cfg, dims)
    lossy = lossy_strata(loss, float(sc["skip_loss_threshold"]))
    out: dict[str, Any] = {**meta, "baseline": base, "arms": arm_names, "ks": ks,
                           "bootstrap": {"iters": iters, "seed": seed}, "coverage_threshold": sc["coverage"],
                           "max_unlocated_share": float(sc.get("max_unlocated_share", 0.01)),
                           "skip_loss": loss, "lossy_strata": lossy, "sets": {}}
    for name, rs in sets.items():
        out["sets"][name] = _set_block(rs, arm_names, base, ks, dims, iters, seed, lossy)
    primary = reference + EXCL_NOISE if noise_info["enabled"] else reference
    fam_of = {r["pmcid"]: r["journal_family"] for r in read_jsonl(out_path(cfg, "sample.jsonl"))}
    out["journal_confusion"] = journal_confusion(sets[primary], arm_names, fam_of)
    out["primary_set"] = primary
    out["noise_exclusion"] = noise_info
    if noise_info["enabled"]:
        out["sensitivity_set"] = reference
        out["sensitivity_label"] = SENSITIVITY_LABEL
    return out


def check_writer_report(cfg: dict[str, Any]) -> dict[str, Any] | None:
    """The check-writer set as a separate secondary report (never used by decide)."""
    qpath = out_path(cfg, "questions.check.jsonl")
    if not qpath.exists():
        return None
    qs = read_jsonl(qpath)
    if not qs:
        return None
    arm_names = [a["name"] for a in cfg_arms(cfg)]
    base = baseline_arm(cfg)["name"]
    sc = cfg["score"]
    ks = [int(k) for k in sc["ks"]]
    iters, seed = int(sc["bootstrap_iters"]), int(sc["bootstrap_seed"])
    rows, meta = build_rows(cfg, arm_names=arm_names, questions=qs, audit_file="audit.check.jsonl")
    sets = {"all": rows}
    if meta.get("audit_present"):
        sets["clean"] = [r for r in rows if not r["ambiguous_gold"]]
    loss = skip_loss(cfg, list(sc["report_dims"]), suffix=".check")
    rep: dict[str, Any] = {k: meta[k] for k in ("n_questions", "n_complete", "missing_by_arm", "audit_present",
                                                "records_without_text", "hit_quality", "refusals",
                                                "request_mismatch_by_arm", "request_unverified_by_arm",
                                                "server_versions")}
    rep["skip_loss"] = loss
    rep["sets"] = {name: _set_block(rs, arm_names, base, ks, [], iters, seed, None) for name, rs in sets.items()}
    rep["primary_set"] = "clean" if "clean" in sets else "all"
    return rep


def run(cfg: dict[str, Any]) -> int:
    require(cfg, "out_dir")
    rows, meta = build_rows(cfg)
    if not rows:
        log("score: no question has an ok retrieval in every arm")
        return 2
    summary = summarise(rows, cfg, meta)  # first: it adds the noise_subset flags to the rows
    write_jsonl(out_path(cfg, "per_question.jsonl"), rows)
    summary["check_writer"] = check_writer_report(cfg)
    write_json(out_path(cfg, "score.json"), summary)
    ps = summary["sets"][summary["primary_set"]]
    k5 = ps["primary"].get("5", next(iter(ps["primary"].values())))["pooled"]
    log("score: " + ", ".join(f"{a} {v['point']:.1f}" for a, v in k5["hit"].items() if v["point"] is not None)
        + f" (hit@5, {summary['primary_set']}, n={ps['n']})")
    if summary.get("sensitivity_set"):
        ss = summary["sets"][summary["sensitivity_set"]]
        log(f"score: {SENSITIVITY_LABEL}: `{summary['sensitivity_set']}`, n={ss['n']}")
    if summary["refusals"]:
        for r in summary["refusals"]:
            log(f"score: REFUSED: {r}")
        return 2
    return 0
