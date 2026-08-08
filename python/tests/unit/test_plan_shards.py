"""Offline tests for the JATS/OA shard planner (#301).

`scripts/plan_shards.py` turns a still-growing, hash-fanned harvest into the
shard files `cwl/jats-ingest.cwl` scatters over. Four properties carry the whole
design, and each is tested here against a synthetic corpus in `tmp_path` — never
against `/rag/oa`, which is a live download:

* **stability** — an article's shard is a pure function of its pmcid. Growing the
  corpus, shuffling the manifest, or excluding half of it must not move a single
  already-assigned article. This is the one that a `count // target` scheme fails
  and the reason the tool exists;
* **refinement under doubling** — with a power-of-two modulus, `n -> 2n` splits
  each shard into exactly two, so a re-plan at finer granularity is a refinement
  of the old plan rather than a reshuffle;
* **balance by work** — shards are budgeted on body+float chars, not file count,
  so one 400k-char article does not silently become the straggler;
* **honest skips** — an article whose file is missing, zero-byte, listed in
  `failures.jsonl`, or empty of text is counted and written out, never dropped.

Everything is filesystem + json; no stores, no embedder, no network.
"""
from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

# plan_shards.py lives under python/scripts.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import plan_shards as ps  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "plan_shards.py"


# --------------------------------------------------------------------------
# synthetic corpus
# --------------------------------------------------------------------------


def make_corpus(root: Path, pmcids, *, body_chars=None, write_files=True,
                failures=(), shuffle_seed=None) -> Path:
    """A miniature harvest: clean/xx/yy/PMC*.xml + manifest.jsonl + failures.jsonl.

    `shuffle_seed` shuffles the manifest line order, which is how the tests prove
    the plan does not depend on it. `body_chars` maps pmcid -> work size; anything
    unlisted gets a size derived from the id so the default corpus is uneven.
    """
    root.mkdir(parents=True, exist_ok=True)
    body_chars = body_chars or {}
    rows = []
    for pmcid in pmcids:
        rel = ps.xml_relpath(pmcid)
        if write_files:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"<article><body><p>{pmcid}</p></body></article>")
        rows.append({
            "pmcid": pmcid,
            "sha256": "0" * 64,
            "source_url": f"https://example.org/{pmcid}.xml",
            "journal_xml": "Synthetic Journal",
            "doi_xml": f"10.0000/{pmcid}",
            "bytes": 1000,
            "body_chars": body_chars.get(pmcid, 1000 + (ps.article_hash(pmcid) % 5000)),
            "back_chars": 500,
            "floats_chars": 0,
        })
    if shuffle_seed is not None:
        random.Random(shuffle_seed).shuffle(rows)
    (root / "manifest.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))
    (root / "failures.jsonl").write_text(
        "".join(json.dumps({"pmcid": p, "err": "not in the OA subset"}) + "\n"
                for p in failures))
    return root


def ids(n, start=1000):
    return [f"PMC{start + i}" for i in range(n)]


def assignment(shards) -> dict[str, int]:
    """pmcid -> shard index, from a plan() result."""
    return {r["pmcid"]: idx for idx, rows in shards.items() for r in rows}


# --------------------------------------------------------------------------
# assignment is a pure function of identity
# --------------------------------------------------------------------------


def test_shard_index_depends_only_on_pmcid_and_modulus():
    # Not Python's salted hash(): that would differ per interpreter process, so a
    # re-plan tomorrow would produce a different corpus layout.
    assert ps.shard_index("PMC13274098", 2048) == ps.shard_index("PMC13274098", 2048)
    out = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {str(SCRIPT.parent)!r}); "
         "import plan_shards as ps; print(ps.shard_index('PMC13274098', 2048))"],
        capture_output=True, text=True, check=True,
    )
    assert int(out.stdout.strip()) == ps.shard_index("PMC13274098", 2048)


def test_xml_relpath_matches_the_harvest_fanout():
    # sha1("PMC13274098") starts 61e9..., and the live harvest holds this article at
    # clean/61/e9/. Relative, so it resolves against the CWL container's --corpus.
    assert ps.xml_relpath("PMC13274098") == "clean/61/e9/PMC13274098.xml"
    assert ps.xml_relpath("PMC13274098", "xml").startswith("xml/61/e9/")


# --------------------------------------------------------------------------
# THE stability property
# --------------------------------------------------------------------------


def test_growth_does_not_move_a_single_already_assigned_article(tmp_path):
    first = ids(300)
    a = make_corpus(tmp_path / "a", first)
    shards_a, _, stats_a = ps.plan(str(a), n_shards=16)
    before = assignment(shards_a)
    assert stats_a["n_planned"] == 300

    # The harvest keeps downloading: 700 more articles, and the manifest is written
    # in a different order for good measure.
    b = make_corpus(tmp_path / "b", first + ids(700, start=5000), shuffle_seed=42)
    shards_b, _, stats_b = ps.plan(str(b), n_shards=16)
    after = assignment(shards_b)

    assert stats_b["n_planned"] == 1000
    assert len(after) == 1000
    moved = {p for p, idx in before.items() if after[p] != idx}
    assert moved == set(), f"{len(moved)} article(s) were reshuffled by corpus growth"


def test_a_count_derived_modulus_would_have_reshuffled(tmp_path):
    """The negative control: shows the property under test is not vacuous.

    `index % ceil(count / target)` is the natural-looking scheme this design
    rejects. Assert it really does churn on the same growth step, so the test above
    is proving something.
    """
    first, grown = ids(300), ids(300) + ids(700, start=5000)

    def naive(pmcids):
        n = max(1, len(pmcids) // 20)
        return {p: i % n for i, p in enumerate(sorted(pmcids))}

    before, after = naive(first), naive(grown)
    assert sum(1 for p in first if after[p] != before[p]) > len(first) // 2


def test_manifest_order_does_not_change_the_shard_files(tmp_path):
    pmcids = ids(400)
    a = make_corpus(tmp_path / "a", pmcids, shuffle_seed=1)
    b = make_corpus(tmp_path / "b", pmcids, shuffle_seed=2)
    ps.main(["--corpus", str(a), "--out", str(tmp_path / "oa"), "--shards", "8"])
    ps.main(["--corpus", str(b), "--out", str(tmp_path / "ob"), "--shards", "8"])
    for f in sorted((tmp_path / "oa").glob("shard-*.jsonl")):
        assert f.read_text() == (tmp_path / "ob" / f.name).read_text()


def test_doubling_the_modulus_splits_shards_instead_of_reshuffling(tmp_path):
    """`n -> 2n` on a power of two is a refinement: shard b becomes b or b+n."""
    c = make_corpus(tmp_path / "c", ids(600))
    coarse, _, _ = ps.plan(str(c), n_shards=8)
    fine, _, _ = ps.plan(str(c), n_shards=16)
    a8, a16 = assignment(coarse), assignment(fine)
    for pmcid, idx in a8.items():
        assert a16[pmcid] in (idx, idx + 8)
    # and the split is real, not a no-op relabelling
    assert len(fine) > len(coarse)


def test_limit_is_hash_ordered_not_manifest_ordered(tmp_path):
    """`--limit` picks by hash, so two writings of the same corpus agree.

    It is also the documented exception to the stability property: growth changes
    *which* articles it picks (a new low-hash arrival displaces the last member),
    though never the shard of an article it does pick.
    """
    a = make_corpus(tmp_path / "a", ids(200), shuffle_seed=1)
    b = make_corpus(tmp_path / "b", ids(200), shuffle_seed=2)
    pa, _, _ = ps.plan(str(a), n_shards=16, limit=50)
    pb, _, _ = ps.plan(str(b), n_shards=16, limit=50)
    assert assignment(pa) == assignment(pb)
    assert sum(len(v) for v in pa.values()) == 50

    grown = make_corpus(tmp_path / "g", ids(200) + ids(800, start=9000))
    pg, _, _ = ps.plan(str(grown), n_shards=16, limit=50)
    survivors = set(assignment(pa)) & set(assignment(pg))
    assert survivors  # membership shifts...
    assert all(assignment(pg)[p] == assignment(pa)[p] for p in survivors)  # ...shards don't


# --------------------------------------------------------------------------
# balance is by work, not by file count
# --------------------------------------------------------------------------


def test_balance_is_measured_in_work_chars_not_articles(tmp_path):
    # Two articles carry 400k chars each; everyone else carries 1k. A count-balanced
    # plan would call these shards equal; the report must not.
    pmcids = ids(200)
    sizes = dict.fromkeys(pmcids, 1000)
    sizes[pmcids[0]] = 400_000
    sizes[pmcids[1]] = 400_000
    c = make_corpus(tmp_path / "c", pmcids, body_chars=sizes)
    shards, _, _ = ps.plan(str(c), n_shards=8)
    report = ps.shard_report(shards, target_chars=100_000)

    loads = [sum(ps.work_chars(r) for r in rows) for rows in shards.values()]
    assert report["work_chars_per_shard"]["max"] == max(loads)
    assert report["total_work_chars"] == sum(loads)
    assert report["work_chars_per_shard"]["max_over_mean"] > 1.5  # the heavy shard shows
    assert report["n_over_target"] >= 1


def test_even_corpus_gives_an_even_plan(tmp_path):
    # With uniform-ish articles the hash buckets are tight; this pins that the
    # spread stays modest rather than merely being reported.
    c = make_corpus(tmp_path / "c", ids(4000))
    shards, _, _ = ps.plan(str(c), n_shards=16)
    report = ps.shard_report(shards, target_chars=10_000_000)
    assert report["n_shards_nonempty"] == 16
    assert report["work_chars_per_shard"]["cv_pct"] < 10.0
    assert report["work_chars_per_shard"]["max_over_mean"] < 1.25


def test_work_chars_counts_body_plus_floats_and_ignores_back(tmp_path):
    assert ps.work_chars({"body_chars": 10, "floats_chars": 5, "back_chars": 999}) == 15
    assert ps.work_chars({}) == 0


# --------------------------------------------------------------------------
# resumability
# --------------------------------------------------------------------------


def test_exclusion_leaves_out_the_done_work_without_moving_the_rest(tmp_path):
    pmcids = ids(400)
    c = make_corpus(tmp_path / "c", pmcids)
    full, _, _ = ps.plan(str(c), n_shards=16)
    before = assignment(full)

    done = set(pmcids[:150])
    rest, _, stats = ps.plan(str(c), n_shards=16, exclude=done)
    after = assignment(rest)

    assert stats["n_excluded"] == 150
    assert stats["n_planned"] == 250
    assert set(after) == set(pmcids) - done
    # the survivors did not move because 150 of their neighbours left
    assert all(after[p] == before[p] for p in after)


def test_round_two_excludes_round_one_by_pointing_at_its_directory(tmp_path):
    c = make_corpus(tmp_path / "c", ids(300))
    r1 = tmp_path / "r1"
    ps.main(["--corpus", str(c), "--out", str(r1), "--shards", "16"])

    # 200 more articles land, then plan round 2 excluding round 1's whole dir.
    make_corpus(tmp_path / "c", ids(300) + ids(200, start=7000))
    r2 = tmp_path / "r2"
    ps.main(["--corpus", str(c), "--out", str(r2), "--shards", "16",
             "--exclude", str(r1)])

    r1_ids = {r["pmcid"] for f in r1.glob("shard-*.jsonl")
              for r in map(json.loads, f.read_text().splitlines())}
    r2_ids = {r["pmcid"] for f in r2.glob("shard-*.jsonl")
              for r in map(json.loads, f.read_text().splitlines())}
    assert len(r1_ids) == 300
    assert len(r2_ids) == 200
    assert not (r1_ids & r2_ids)
    # same buckets across rounds: a shard number means the same thing in both
    plan2 = json.loads((r2 / "plan.json").read_text())
    for entry in plan2["shards"]:
        rows = [json.loads(x) for x in
                (r2 / entry["file"]).read_text().splitlines()]
        assert all(ps.shard_index(r["pmcid"], 16) == entry["shard"] for r in rows)


def test_excluding_a_plan_dir_does_not_exclude_its_skips(tmp_path):
    """A skip is a retry, not a decision — the harvest is still downloading.

    Round 1 skips an article whose XML has not landed. Once it lands, round 2 must
    plan it, so pointing --exclude at round 1's directory must pick up its shard
    files and leave `skipped.jsonl` alone.
    """
    root = tmp_path / "c"
    make_corpus(root, ids(40))
    late = root / ps.xml_relpath("PMC1007")
    late.unlink()

    r1 = tmp_path / "r1"
    ps.main(["--corpus", str(root), "--out", str(r1), "--shards", "8"])
    assert json.loads((r1 / "plan.json").read_text())["counts"]["skips"]["missing"] == 1

    late.write_text("<article/>")  # the download finishes
    r2 = tmp_path / "r2"
    ps.main(["--corpus", str(root), "--out", str(r2), "--shards", "8",
             "--exclude", str(r1)])
    r2_ids = {r["pmcid"] for f in r2.glob("shard-*.jsonl")
              for r in map(json.loads, f.read_text().splitlines())}
    assert r2_ids == {"PMC1007"}
    assert ps.shard_index("PMC1007", 8) == int(
        next(r2.glob("shard-*.jsonl")).stem.split("-")[1])


@pytest.mark.parametrize("shape", ["plain", "jsonl"])
def test_exclusion_accepts_a_plain_list_or_any_jsonl_with_pmcid(tmp_path, shape):
    pmcids = ids(100)
    c = make_corpus(tmp_path / "c", pmcids)
    done, ex = pmcids[:30], tmp_path / "done.txt"
    if shape == "plain":
        ex.write_text("# already ingested\n" + "\n".join(done) + "\n")
    else:
        ex.write_text("".join(
            json.dumps({"pmcid": p, "reason": "already ingested"}) + "\n" for p in done))
    _, _, stats = ps.plan(str(c), n_shards=8, exclude=ps.read_exclusions([str(ex)]))
    assert stats["n_excluded"] == 30
    assert stats["n_planned"] == 70


def test_replanning_into_a_used_directory_is_refused_without_force(tmp_path):
    c = make_corpus(tmp_path / "c", ids(50))
    out = tmp_path / "out"
    ps.main(["--corpus", str(c), "--out", str(out), "--shards", "4"])
    with pytest.raises(SystemExit, match="already holds"):
        ps.main(["--corpus", str(c), "--out", str(out), "--shards", "4"])
    # --dry-run never writes, so it is always allowed
    assert ps.main(["--corpus", str(c), "--out", str(out), "--shards", "4",
                    "--dry-run"]) == 0
    assert ps.main(["--corpus", str(c), "--out", str(out), "--shards", "4",
                    "--force"]) == 0


# --------------------------------------------------------------------------
# nothing is dropped silently
# --------------------------------------------------------------------------


def test_every_kind_of_unusable_article_is_counted_and_written_out(tmp_path):
    pmcids = ids(60)
    root = tmp_path / "c"
    make_corpus(root, pmcids, body_chars={pmcids[5]: 0},
                failures=[pmcids[0], "PMC999999"])
    # the file for one article never landed; another landed empty
    (root / ps.xml_relpath(pmcids[1])).unlink()
    (root / ps.xml_relpath(pmcids[2])).write_text("")
    # ...and one manifest line is corrupt
    with (root / "manifest.jsonl").open("a") as fh:
        fh.write("{not json at all\n")

    out = tmp_path / "out"
    ps.main(["--corpus", str(root), "--out", str(out), "--shards", "8"])
    plan_doc = json.loads((out / "plan.json").read_text())
    counts = plan_doc["counts"]

    assert counts["skips"] == {"failed_fetch": 1, "missing": 1, "empty_file": 1,
                               "no_work": 1, "bad_manifest_line": 1}
    assert counts["n_skipped"] == 5
    assert counts["n_planned"] == len(pmcids) - 4
    # a pmcid that failed to fetch and never reached the manifest is reported too,
    # separately — it is not a skipped row, it is an article that does not exist
    assert counts["n_failures_not_in_manifest"] == 1

    skips = [json.loads(x) for x in (out / "skipped.jsonl").read_text().splitlines()]
    assert len(skips) == 5
    assert {s["reason"] for s in skips} == set(ps.SKIP_REASONS)
    # every skipped article is identifiable, and none of them is in a shard
    planned = {r["pmcid"] for f in out.glob("shard-*.jsonl")
               for r in map(json.loads, f.read_text().splitlines())}
    for s in skips:
        if s["pmcid"]:
            assert s["pmcid"] not in planned
    # the arithmetic closes: nothing vanished between the manifest and the plan
    assert (counts["n_planned"] + counts["n_skipped"] + counts["n_excluded"]
            + counts["n_duplicate_pmcid_rows"]) == counts["n_manifest_rows"]


def test_a_corpus_whose_layout_does_not_match_the_fanout_is_refused(tmp_path):
    # Every file flat in one directory instead of hash-fanned: the planner must say
    # so, not emit 200 shard lines pointing at paths that do not exist.
    root = tmp_path / "c"
    make_corpus(root, ids(200), write_files=False)
    flat = root / "clean"
    flat.mkdir(parents=True, exist_ok=True)
    for p in ids(200):
        (flat / f"{p}.xml").write_text("<article/>")
    with pytest.raises(SystemExit, match="fanout convention"):
        ps.plan(str(root), n_shards=8)


def test_no_verify_files_trades_the_missing_check_for_speed(tmp_path):
    root = tmp_path / "c"
    make_corpus(root, ids(20))
    (root / ps.xml_relpath("PMC1001")).unlink()
    _, _, verified = ps.plan(str(root), n_shards=4)
    assert verified["skips"]["missing"] == 1
    _, _, trusted = ps.plan(str(root), n_shards=4, verify_files=False)
    assert trusted["skips"]["missing"] == 0
    assert trusted["n_planned"] == 20


def test_duplicate_manifest_rows_collapse_to_one_article(tmp_path):
    root = tmp_path / "c"
    make_corpus(root, ids(20))
    with (root / "manifest.jsonl").open("a") as fh:
        fh.write(json.dumps({"pmcid": "PMC1000", "body_chars": 99}) + "\n")
    shards, _, stats = ps.plan(str(root), n_shards=4)
    assert stats["n_duplicate_pmcid_rows"] == 1
    assert stats["n_planned"] == 20
    rows = [r for v in shards.values() for r in v if r["pmcid"] == "PMC1000"]
    assert len(rows) == 1
    assert rows[0]["body_chars"] != 99  # first-wins, so order cannot change the plan


# --------------------------------------------------------------------------
# output contract + CLI
# --------------------------------------------------------------------------


def test_shard_lines_are_the_self_contained_records_jats_extract_reads(tmp_path):
    c = make_corpus(tmp_path / "c", ids(40))
    out = tmp_path / "out"
    ps.main(["--corpus", str(c), "--out", str(out), "--shards", "4"])
    files = sorted(out.glob("shard-*.jsonl"))
    assert files and [f.name for f in files] == [ps.shard_filename(i)
                                                 for i in range(len(files))]
    for f in files:
        for line in f.read_text().splitlines():
            row = json.loads(line)
            # jats_extract's --shard contract: xml_path (relative, resolved against
            # --corpus) + pmcid, and the manifest row travels along so a worker
            # never opens the corpus-wide manifest.
            assert row["xml_path"] == ps.xml_relpath(row["pmcid"])
            assert (c / row["xml_path"]).exists()
            assert row["sha256"] and row["source_url"] and row["doi_xml"]
    # no header line: the file is pure JSONL, so it stays splittable/concatenable
    assert json.loads(files[0].read_text().splitlines()[0])["pmcid"].startswith("PMC")


def test_plan_json_records_the_assignment_rule_and_the_totals(tmp_path):
    c = make_corpus(tmp_path / "c", ids(120))
    out = tmp_path / "out"
    ps.main(["--corpus", str(c), "--out", str(out), "--shards", "8"])
    doc = json.loads((out / "plan.json").read_text())
    assert doc["schema"] == ps.SCHEMA
    # the modulus travels with the plan: a later round that used a different one
    # would give the shard numbers a different meaning
    assert doc["assignment"]["n_shards"] == 8
    assert doc["assignment"]["hash"] == "sha1"
    assert doc["counts"]["n_planned"] == 120
    assert sum(e["n_articles"] for e in doc["shards"]) == 120
    assert doc["distribution"]["n_articles"] == 120
    for e in doc["shards"]:
        assert (out / e["file"]).exists()


def test_dry_run_reports_but_writes_nothing(tmp_path, capsys):
    c = make_corpus(tmp_path / "c", ids(80))
    out = tmp_path / "out"
    assert ps.main(["--corpus", str(c), "--out", str(out), "--shards", "8",
                    "--dry-run"]) == 0
    assert not out.exists()
    text = capsys.readouterr().out
    assert "planned" in text and "shards" in text and "skipped" in text
    assert "CV" in text and "worst/mean" in text


def test_a_non_power_of_two_modulus_is_refused(tmp_path):
    c = make_corpus(tmp_path / "c", ids(10))
    with pytest.raises(SystemExit, match="power of two"):
        ps.main(["--corpus", str(c), "--out", str(tmp_path / "o"), "--shards", "1000"])


def test_a_corpus_without_a_manifest_fails_clearly(tmp_path):
    (tmp_path / "c").mkdir()
    with pytest.raises(SystemExit, match="no manifest.jsonl"):
        ps.plan(str(tmp_path / "c"), n_shards=4)


def test_missing_exclusion_source_fails_clearly(tmp_path):
    with pytest.raises(SystemExit, match="no such file"):
        ps.read_exclusions([str(tmp_path / "nope.txt")])


def test_runs_as_a_subprocess_like_the_other_repo_scripts(tmp_path):
    c = make_corpus(tmp_path / "c", ids(60))
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--corpus", str(c),
         "--out", str(tmp_path / "out"), "--shards", "8", "--dry-run"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "dry run" in r.stdout
