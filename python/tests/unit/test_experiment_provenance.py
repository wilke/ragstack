"""Experiment provenance (owner decision 2026-10-06): ``ragstack.provenance.experiment_provenance``.

Replaces "``chunkers.py`` is frozen by the chunking study" with "every run records
which code it was". Each case builds a real temporary repository (or none, or a
fake ``RELEASE`` file) and checks the record against git, not against a regex:

* clean checkout on / past a tag  → derived version, raw describe, full commit, citable
* dirty checkout                   → no version, ``dirty tree`` warning, not citable
* inside a tools image (no git)    → version/commit from ``RELEASE``, ``source="image"``
* no git at all                    → nulls + ``no git checkout``, never an exception
* JSON round-trip, the CLI, the span fingerprints, and the rule that only
  ``provenance.py`` reads the raw describe out of ``version.py``.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ragstack import provenance as p
from ragstack import version as v

REPO = Path(__file__).resolve().parents[3]
PY = REPO / "python"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.invalid")
    _git(r, "config", "user.name", "t")
    (r / "f").write_text("one\n")
    _git(r, "add", "f")
    _git(r, "commit", "-q", "-m", "one")
    _git(r, "tag", "v1.0.0")
    return r


@pytest.fixture
def no_release(tmp_path: Path) -> Path:
    return tmp_path / "absent-RELEASE"


@pytest.fixture
def release(tmp_path: Path) -> Path:
    f = tmp_path / "RELEASE"
    f.write_text(
        "version=v1.6.4\ncommit=" + "a" * 40 + "\nbuild=3\nbuild_date=2026-10-01T00:00:00Z\n"
    )
    return f


def _warn(rec: dict, prefix: str) -> bool:
    return any(w.startswith(prefix) for w in rec["warnings"])


def test_clean_checkout_on_a_tag(repo, no_release):
    rec = p.experiment_provenance(repo=repo, release_path=no_release)
    assert rec["source"] == "git"
    assert rec["version"] == "v1.0.0"
    assert rec["describe"] == _git(repo, "describe", "--tags", "--match", "v*", "--long",
                                   "--dirty", "--always")
    assert re.fullmatch(r"v1\.0\.0-0-g[0-9a-f]+", rec["describe"])
    assert rec["commit"] == _git(repo, "rev-parse", "HEAD")
    assert len(rec["commit"]) == 40
    assert rec["dirty"] is False
    assert rec["citable"] is True
    assert rec["in_image"] is False and rec["image"] is None
    assert _warn(rec, p.WARN_NOT_IN_IMAGE)
    assert not _warn(rec, p.WARN_DIRTY) and not _warn(rec, p.WARN_NO_GIT)


def test_clean_checkout_past_a_tag_records_version_describe_and_full_commit(repo, no_release):
    (repo / "f").write_text("two\n")
    _git(repo, "commit", "-q", "-am", "two")
    short = _git(repo, "rev-parse", "--short", "HEAD")
    rec = p.experiment_provenance(repo=repo, release_path=no_release)
    assert rec["version"] == f"v1.0.0+{short}"
    # The raw describe is kept verbatim as provenance, next to (not instead of)
    # the derived version — and it is not itself a version.
    assert rec["describe"] == f"v1.0.0-1-g{short}"
    with pytest.raises(ValueError):
        v.split_version(rec["describe"])
    assert rec["commit"] == _git(repo, "rev-parse", "HEAD")


def test_untracked_file_is_not_citable(repo, no_release):
    # `git describe --dirty` cannot see it; an uncommitted harness looks like this.
    (repo / "harness.py").write_text("print('hi')\n")
    rec = p.experiment_provenance(repo=repo, release_path=no_release)
    assert rec["dirty"] is False
    assert rec["untracked"] == 1
    assert rec["citable"] is False
    assert _warn(rec, p.WARN_UNTRACKED)
    assert v.untracked_count(repo) == 1


def test_ignored_files_are_not_untracked(repo, no_release):
    (repo / ".gitignore").write_text("*.log\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore")
    (repo / "run.log").write_text("noise\n")
    rec = p.experiment_provenance(repo=repo, release_path=no_release)
    assert rec["untracked"] == 0 and rec["citable"] is True


def test_dirty_checkout_has_no_version_and_is_not_citable(repo, no_release):
    (repo / "f").write_text("uncommitted\n")
    rec = p.experiment_provenance(repo=repo, release_path=no_release)
    assert rec["source"] == "git"
    assert rec["dirty"] is True
    assert rec["version"] is None
    assert rec["describe"].endswith("-dirty")
    assert rec["commit"] == _git(repo, "rev-parse", "HEAD")
    assert rec["citable"] is False
    assert _warn(rec, p.WARN_DIRTY)


def test_fake_release_file_is_the_image_case(tmp_path, release):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    rec = p.experiment_provenance(repo=not_a_repo, release_path=release)
    assert rec["source"] == "image"
    assert rec["in_image"] is True
    assert rec["image"] == {"version": "v1.6.4", "commit": "a" * 40, "build": "3",
                            "build_date": "2026-10-01T00:00:00Z"}
    assert rec["version"] == "v1.6.4" and rec["commit"] == "a" * 40
    assert rec["describe"] is None
    assert rec["citable"] is True
    assert _warn(rec, p.WARN_NO_GIT)
    assert not _warn(rec, p.WARN_NOT_IN_IMAGE)


def test_checkout_and_image_both_present_records_the_checkout(repo, release):
    rec = p.experiment_provenance(repo=repo, release_path=release)
    assert rec["source"] == "git"
    assert rec["image"]["commit"] == "a" * 40
    assert rec["commit"] == _git(repo, "rev-parse", "HEAD")
    assert any("differs from the image" in w for w in rec["warnings"])


def test_no_git_at_all_never_raises(tmp_path, monkeypatch, no_release):
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    rec = p.experiment_provenance(repo=tmp_path, release_path=no_release)
    assert rec["source"] == "distribution"
    assert rec["version"] is None and rec["describe"] is None and rec["commit"] is None
    assert rec["dirty"] is None and rec["untracked"] is None
    assert rec["citable"] is False
    assert _warn(rec, p.WARN_NO_GIT) and _warn(rec, p.WARN_NOT_IN_IMAGE)


def test_a_failing_lookup_is_a_warning_not_an_exception(repo, no_release, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(v, "raw_describe_for_provenance", boom)
    monkeypatch.setattr(p, "sentence_spans_fingerprint", boom)
    rec = p.experiment_provenance(repo=repo, release_path=no_release)
    assert rec["version"] is None and rec["citable"] is False
    assert any(w.startswith("provenance: git lookup failed") for w in rec["warnings"])
    assert any(w.startswith("provenance: sentence fingerprint failed") for w in rec["warnings"])


def test_record_is_json_round_trippable(repo, release):
    rec = p.experiment_provenance(repo=repo, release_path=release)
    assert json.loads(json.dumps(rec)) == rec
    for key in ("version", "describe", "commit", "dirty", "untracked", "source", "image",
                "installed_distribution", "python", "host", "recorded_at", "warnings",
                "segmentation", "citable", "schema"):
        assert key in rec
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", rec["recorded_at"])


def test_this_checkout_by_default():
    rec = p.experiment_provenance(release_path="/nonexistent/RELEASE")
    if rec["source"] == "git":
        assert rec["commit"] == _git(REPO, "rev-parse", "HEAD")


# --------------------------------------------------------------------------- #
# Span fingerprints: the coordinate system labels are keyed by
# --------------------------------------------------------------------------- #


def test_sentence_fingerprint_is_deterministic_and_text_sensitive():
    a = p.sentence_spans_fingerprint(["One. Two. Three."])
    b = p.sentence_spans_fingerprint(["One. Two. Three."])
    c = p.sentence_spans_fingerprint(["One. Two three."])
    assert a == b
    assert a["kind"] == "sentences" and a["n_docs"] == 1 and a["n_spans"] == 3
    assert a["backend"] in ("punkt", "regex")
    assert a["sha256"] != c["sha256"] and a["texts_sha256"] != c["texts_sha256"]


def test_span_fingerprint_is_generic_over_kinds():
    spans = [[(0, 400), (400, 900)], [(0, 512)]]
    units = p.span_fingerprint(spans, kind="units", producer="pilot_common.units_for_article")
    assert units["kind"] == "units" and units["n_docs"] == 2 and units["n_spans"] == 3
    assert units["texts_sha256"] is None
    moved = p.span_fingerprint([[(0, 401), (401, 900)], [(0, 512)]], kind="units")
    assert moved["sha256"] != units["sha256"]
    # The kind is part of the hash: equal offsets of different things are not equal.
    assert p.span_fingerprint(spans, kind="sentences")["sha256"] != units["sha256"]


def test_experiment_provenance_takes_run_texts_and_extra_fingerprints(repo, no_release):
    units = p.span_fingerprint([[(0, 3)]], kind="units")
    rec = p.experiment_provenance(texts=["A b. C d."], segmentations=[units],
                                  repo=repo, release_path=no_release)
    kinds = [(s["kind"], s.get("sample")) for s in rec["segmentation"]]
    assert kinds == [("sentences", "run"), ("units", None)]
    canon = p.experiment_provenance(repo=repo, release_path=no_release)["segmentation"]
    assert canon[0]["sample"] == "canonical-1"
    assert canon[0]["n_docs"] == len(p.CANONICAL_SEGMENTATION_SAMPLE)


def test_canonical_sample_exercises_subsplit_and_backend_disagreement():
    from ragstack.ingestion import chunkers

    sample = p.CANONICAL_SEGMENTATION_SAMPLE
    long_ = [t for t in sample if len(t) > chunkers._LONG_SPAN_CHARS]
    assert long_ and not any(ch in long_[0] for ch in ".!?")
    assert all(sep in long_[0] for sep in ("\n", "\t", ";"))
    assert len(chunkers.sentence_spans(long_[0])) > 1  # _subsplit_long_spans ran
    if chunkers._punkt_sentence_spans("A b. C d.") is not None:
        assert any(chunkers._punkt_sentence_spans(t) != chunkers._fallback_sentence_spans(t)
                   for t in sample)


# --------------------------------------------------------------------------- #
# Comparison key and path-free warnings
# --------------------------------------------------------------------------- #


def test_comparison_key_ignores_context(repo, no_release):
    a = p.experiment_provenance(repo=repo, release_path=no_release)
    b = dict(a, host="elsewhere", recorded_at="2000-01-01T00:00:00Z", python="0.0",
             installed_distribution="9.9", warnings=["x"])
    assert p.comparison_key(a) == p.comparison_key(b)
    assert set(p.comparison_key(a)) == set(p.EXPERIMENT_COMPARISON_KEYS)
    assert set(p.comparison_key(a)["segmentation"][0]) == {
        "kind", "sha256", "texts_sha256", "backend", "nltk"}
    c = dict(a, commit="b" * 40)
    assert p.comparison_key(c) != p.comparison_key(a)


def test_warnings_never_embed_caller_paths(tmp_path):
    secret = tmp_path / "very-private-dir"
    secret.mkdir()
    rec = p.experiment_provenance(repo=secret, release_path=secret / "RELEASE")
    assert rec["warnings"]
    assert not any(str(tmp_path) in w or "very-private-dir" in w for w in rec["warnings"])


def test_entry_point_outside_checkout_is_warned(repo, no_release, tmp_path, monkeypatch):
    script = tmp_path / "elsewhere.py"
    script.write_text("")
    monkeypatch.setattr(sys, "argv", [str(script)])
    rec = p.experiment_provenance(repo=repo, release_path=no_release)
    assert _warn(rec, p.WARN_ENTRY_POINT)
    assert str(tmp_path) not in " ".join(rec["warnings"])
    inside = repo / "f"
    monkeypatch.setattr(sys, "argv", [str(inside)])
    assert not _warn(p.experiment_provenance(repo=repo, release_path=no_release),
                     p.WARN_ENTRY_POINT)


# --------------------------------------------------------------------------- #
# CLI and the one-caller rule
# --------------------------------------------------------------------------- #


def test_cli_prints_the_record(repo, no_release):
    out = subprocess.run(
        [sys.executable, "-m", "ragstack.provenance", "--experiment", "--repo", str(repo),
         "--release-path", str(no_release)],
        capture_output=True, text=True, cwd=PY,
        env={"PYTHONPATH": str(PY), "PATH": subprocess.os.environ["PATH"]},
    )
    assert out.returncode == 0, out.stderr
    rec = json.loads(out.stdout)
    assert rec["version"] == "v1.0.0" and rec["commit"] == _git(repo, "rev-parse", "HEAD")


def test_only_provenance_reads_the_raw_describe():
    """``raw_describe_for_provenance`` is the one labelled exit for the raw
    describe string (ADR-0010 decision 1 + the experiment-provenance exception);
    ``experiment_provenance`` is its only caller."""
    allowed = {PY / "ragstack" / "version.py", PY / "ragstack" / "provenance.py"}
    offenders = [
        str(f.relative_to(REPO)) for f in PY.rglob("*.py")
        if f not in allowed and "tests" not in f.relative_to(PY).parts
        and "raw_describe_for_provenance" in f.read_text(encoding="utf-8", errors="replace")
    ]
    assert offenders == []
