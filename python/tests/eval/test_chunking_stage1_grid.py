"""Unit tests for the stage-1 chunking config grid (docs/plans/chunking-evaluation.md).

Everything here is pure arithmetic over the config dataclasses: no tokenizer, no
store, no embedding endpoint. That is deliberate and it is also forced — the
mandated test interpreter has no ``transformers``, so a test that built a real
``HFTokenCounter`` would not run. It does not need one: the grid is a function of
``(kind, size, overlap_fraction)`` and nothing in it reads a corpus.

**What these tests are defending.** ``chunk_overlap`` is an absolute token count,
so holding it at 64 across a size ladder does *not* hold overlap constant — 64
tokens is 25.0% at size 256 and 3.1% at size 2048. A size sweep at a fixed
absolute overlap confounds the size effect with a fading overlap effect and
cannot separate them, and separating them is the entire purpose of stage 1. So
overlap here is parameterised as a **fraction** and resolved per size. The test
that would go red if someone reverted that is
``test_one_fraction_yields_four_different_absolutes`` — see its docstring.

The eval scripts live under ``python/scripts/eval`` and import each other as
siblings, so the directory goes on ``sys.path`` the same way the harnesses do.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import pytest

_EVAL_DIR = Path(__file__).resolve().parents[2] / "scripts" / "eval"
sys.path.insert(0, str(_EVAL_DIR))

import chunking_compare_7way as c7  # noqa: E402
import scifact_chunk_eval as sfe  # noqa: E402

#: The 7-way harness's config set as it stood before the grid was added. Pinned
#: literally rather than derived, so that "do not break the existing configs"
#: is checked against a written-down list instead of against whatever the code
#: currently happens to produce.
LEGACY_KEYS = [
    "fixed_char512",
    "fixed_char2048",
    "fixed_tok256",
    "fixed_tok512",
    "sentence_tok512",
    "words_tok512",
    "semantic_tokcap",
    "semantic_pooled",
]


# --------------------------------------------------------------------------- #
# Shape: exactly 24 configs, unique keys
# --------------------------------------------------------------------------- #
def test_grid_generates_exactly_24_configs():
    assert len(c7.STAGE1_CONFIGS) == 24


def test_grid_keys_are_unique():
    keys = [c.key for c in c7.STAGE1_CONFIGS]
    assert len(set(keys)) == 24, f"duplicate keys: {sorted(k for k in keys if keys.count(k) > 1)}"


def test_grid_is_twelve_token_window_cells_plus_twelve_other_kind_cells():
    fixed = [c for c in c7.STAGE1_CONFIGS if c.kind == "token_window"]
    other = [c for c in c7.STAGE1_CONFIGS if c.kind != "token_window"]
    assert len(fixed) == 12
    assert len(other) == 12
    # 4 sizes x 3 overlap fractions, every cell present exactly once.
    assert {(c.size, c.overlap_frac) for c in fixed} == {
        (s, f) for s in c7.STAGE1_SIZES for f in c7.STAGE1_OVERLAP_FRACS
    }
    # 3 kinds x 4 sizes, all at the single other-kinds fraction.
    assert {(c.kind, c.size) for c in other} == {
        (k, s) for k in c7.STAGE1_OTHER_KINDS for s in c7.STAGE1_SIZES
    }
    assert {c.overlap_frac for c in other} == {c7.STAGE1_OTHER_FRAC}


def test_the_grid_dimensions_are_the_ones_the_plan_names():
    assert c7.STAGE1_SIZES == (256, 512, 1024, 2048)
    assert c7.STAGE1_OVERLAP_FRACS == (0.0, 0.125, 0.25)
    assert c7.STAGE1_OTHER_KINDS == ("sentence", "words", "semantic")
    # The plan does not pin the other-kinds overlap; 12.5% is this module's
    # choice and it is the shipping default's fraction (512/64).
    assert c7.STAGE1_OTHER_FRAC == 0.125


def test_zero_overlap_is_present_at_every_size():
    """The plan: "Include 0% at every size. It is the cheapest configuration, so
    the burden of proof sits on overlap." """
    zero = {c.size for c in c7.STAGE1_CONFIGS if c.overlap_frac == 0.0}
    assert zero == set(c7.STAGE1_SIZES)
    assert all(c.overlap == 0 for c in c7.STAGE1_CONFIGS if c.overlap_frac == 0.0)


# --------------------------------------------------------------------------- #
# Fraction -> absolute resolution
# --------------------------------------------------------------------------- #
#: The full expected table. Written out rather than recomputed, so a change to
#: the resolution rule has to be typed here too.
EXPECTED_OVERLAP = {
    (256, 0.0): 0, (256, 0.125): 32, (256, 0.25): 64,
    (512, 0.0): 0, (512, 0.125): 64, (512, 0.25): 128,
    (1024, 0.0): 0, (1024, 0.125): 128, (1024, 0.25): 256,
    (2048, 0.0): 0, (2048, 0.125): 256, (2048, 0.25): 512,
}


@pytest.mark.parametrize(("size", "frac"), sorted(EXPECTED_OVERLAP))
def test_resolve_overlap_tokens_matches_the_expected_table(size, frac):
    assert c7.resolve_overlap_tokens(size, frac) == EXPECTED_OVERLAP[(size, frac)]


def test_generated_configs_carry_the_resolved_absolute(*, table=EXPECTED_OVERLAP):
    for cfg in c7.STAGE1_CONFIGS:
        assert cfg.overlap_frac is not None, f"{cfg.key} has no overlap fraction"
        expected = table[(cfg.size, cfg.overlap_frac)]
        assert cfg.overlap == expected, (
            f"{cfg.key}: overlap {cfg.overlap} != {expected} "
            f"({cfg.overlap_frac:.3%} of {cfg.size})"
        )


def test_resolve_overlap_rejects_a_fraction_that_stalls_the_window():
    # f >= 1 means the window never advances; inflation is 1/(1-f).
    for bad in (1.0, 1.5, -0.1):
        with pytest.raises(ValueError):
            c7.resolve_overlap_tokens(512, bad)


def test_resolution_rounds_half_up_not_to_even():
    """No shipping cell lands on a .5 boundary, so this pins the stated rule for
    whoever adds a size or a fraction later. ``round()`` would give 2 and 4."""
    assert c7.resolve_overlap_tokens(10, 0.25) == 3   # 2.5 -> 3, not 2
    assert c7.resolve_overlap_tokens(30, 0.125) == 4  # 3.75 -> 4
    assert c7.resolve_overlap_tokens(14, 0.25) == 4   # 3.5 -> 4


# --------------------------------------------------------------------------- #
# THE anti-confound assertion
# --------------------------------------------------------------------------- #
def test_overlap_is_the_same_proportion_at_every_size():
    """Overlap means the same thing at every rung of the size ladder.

    This is the property a fixed absolute overlap does not have, and the reason
    the grid exists. Asserted on the generated configs, not on the helper, so it
    also covers a cell that hardcoded its overlap past the helper.
    """
    for cfg in c7.STAGE1_CONFIGS:
        assert cfg.overlap / cfg.size == pytest.approx(cfg.overlap_frac), (
            f"{cfg.key}: {cfg.overlap}/{cfg.size} = "
            f"{cfg.overlap / cfg.size:.4%}, not {cfg.overlap_frac:.4%}"
        )


def test_one_fraction_yields_four_different_absolutes():
    """The narrow mutation this suite exists to catch.

    Reintroducing absolute-overlap semantics — pinning every size to
    ``overlap=64``, or dropping the ``* size`` from the resolution — collapses
    this set to one value and turns this red. It is stated as an inequality
    because "the numbers differ across sizes" is precisely what "the proportion
    is held constant" means once the sizes differ.
    """
    for frac in c7.STAGE1_OVERLAP_FRACS:
        if frac == 0.0:
            continue  # 0% is 0 tokens at every size, correctly
        absolutes = {
            c.overlap for c in c7.STAGE1_CONFIGS
            if c.kind == "token_window" and c.overlap_frac == frac
        }
        assert len(absolutes) == len(c7.STAGE1_SIZES), (
            f"at {frac:.1%} the ladder resolved to {sorted(absolutes)} — a single "
            f"repeated value means overlap is absolute again, and the size effect "
            f"is confounded with a fading overlap effect"
        )


def test_a_fixed_absolute_overlap_would_be_a_different_proportion_at_each_size():
    """The confound, stated as arithmetic — the table in the plan.

    Not a test of our code; a test that the premise still holds, so the reason
    for the whole design is written down next to the design.
    """
    assert [64 / s for s in c7.STAGE1_SIZES] == pytest.approx(
        [0.25, 0.125, 0.0625, 0.03125]
    )


# --------------------------------------------------------------------------- #
# The shipping control falls out of the grid unchanged
# --------------------------------------------------------------------------- #
def test_fixed_tok512_is_numerically_512_over_64_at_12_5_percent():
    cfg = c7.STAGE1_CONFIG_BY_KEY["fixed_tok512"]
    assert (cfg.kind, cfg.size, cfg.overlap) == ("token_window", 512, 64)
    assert cfg.overlap_frac == 0.125
    assert 64 / 512 == 0.125  # the identity that lets it be a cell of this grid


def test_fixed_tok512_keeps_its_key_and_is_the_very_same_config_object():
    """One definition, so the grid cell and the 7-way config can never drift into
    two different chunkings sharing one collection name."""
    assert "fixed_tok512" in c7.STAGE1_CONFIG_KEYS
    assert "fixed_tok512" in c7.CONFIG_KEYS
    assert c7.CONFIG_BY_KEY["fixed_tok512"] is c7.STAGE1_CONFIG_BY_KEY["fixed_tok512"]
    assert c7.STATS_REFERENCE == "fixed_tok512"


def test_the_grid_reuses_exactly_one_legacy_key():
    """Only the shipping control is aliased. Everything else gets a uniform key,
    so a grid row is never mistaken for a legacy row."""
    assert set(c7.STAGE1_CONFIG_KEYS) & set(c7.CONFIG_KEYS) == {"fixed_tok512"}
    assert len(c7.ALL_CONFIGS) == len(LEGACY_KEYS) + 23
    assert len(set(c7.ALL_CONFIG_KEYS)) == len(c7.ALL_CONFIG_KEYS)


def test_the_legacy_seven_way_set_is_unchanged():
    """``CONFIGS`` is what an invocation with no --configs runs. The grid must not
    grow it, or the 7-way harness silently becomes a 31-way run."""
    assert c7.CONFIG_KEYS == LEGACY_KEYS


@pytest.mark.parametrize(
    ("legacy_key", "grid_key"),
    [
        ("fixed_tok256", "fixed_tok256_ov12_5pct"),
        ("sentence_tok512", "sentence_tok512_ov12_5pct"),
        ("words_tok512", "words_tok512_ov12_5pct"),
    ],
)
def test_other_shipping_configs_also_fall_out_of_the_grid_identically(
    legacy_key, grid_key
):
    """Corroboration, not a requirement: three more committed configs turn out to
    be 12.5% cells too, and the grid reproduces the parameters that actually
    reach the chunker for each. They keep uniform grid keys (only the stats
    reference is aliased), so this compares parameters, not names.
    """
    legacy = c7.CONFIG_BY_KEY[legacy_key]
    grid = c7.STAGE1_CONFIG_BY_KEY[grid_key]
    assert (grid.kind, grid.size) == (legacy.kind, legacy.size)
    assert grid.char_overlap == legacy.char_overlap
    if legacy.kind == "token_window":
        assert grid.overlap == legacy.overlap


def test_sentence_char_overlap_matches_the_shipping_config():
    """The sentence/words packer takes its overlap in CHARS, so a token overlap
    has to be rendered at some chars-per-token. 2.5 is the constant the committed
    ``sentence_tok512`` / ``words_tok512`` were written with (64 tok -> 160
    chars). Changing it to the measured 3.50 would silently re-cut every
    sentence/words config, so it is pinned here rather than left to a comment.
    """
    assert c7.OVERLAP_CHARS_PER_TOKEN == 2.5
    assert c7.STAGE1_CONFIG_BY_KEY["sentence_tok512_ov12_5pct"].char_overlap == 160
    assert c7.STAGE1_CONFIG_BY_KEY["words_tok512_ov12_5pct"].char_overlap == 160
    # And it scales with the resolved token overlap, not with a constant.
    assert [
        c7.STAGE1_CONFIG_BY_KEY[f"sentence_tok{s}_ov12_5pct"].char_overlap
        for s in c7.STAGE1_SIZES
    ] == [80, 160, 320, 640]


# --------------------------------------------------------------------------- #
# Semantic: the budget reaches its fallback, and the legacy configs are untouched
# --------------------------------------------------------------------------- #
def test_semantic_grid_cells_thread_the_grid_budget_into_the_fallback_window():
    """SemanticChunker is adaptive and has no overlap of its own; ``size`` is its
    token cap and chunk_size/chunk_overlap reach only its oversized-doc
    fixed-token fallback. Threading them there is what keeps the fallback on the
    grid's budget instead of make_chunker's 512/64 defaults."""
    for size in c7.STAGE1_SIZES:
        cfg = c7.STAGE1_CONFIG_BY_KEY[f"semantic_tok{size}_ov12_5pct"]
        assert cfg.extra["chunk_size"] == size
        assert cfg.extra["chunk_overlap"] == cfg.overlap
        # The truncation policy stays declared and identical to the shipping one.
        for k, v in c7.SEMANTIC_POLICY.items():
            assert cfg.extra[k] == v


def test_semantic_labels_admit_that_overlap_only_reaches_the_fallback():
    cfg = c7.STAGE1_CONFIG_BY_KEY["semantic_tok1024_ov12_5pct"]
    assert "fallback" in cfg.label
    assert "adaptive" in cfg.label


def test_the_shipping_semantic_configs_are_bit_identical():
    """The grid threads chunk_size/chunk_overlap through ``extra``; the legacy
    semantic configs carry no such keys, so their chunker construction is
    unchanged."""
    for key in ("semantic_tokcap", "semantic_pooled"):
        extra = c7.CONFIG_BY_KEY[key].extra
        assert extra == c7.SEMANTIC_POLICY
        assert "chunk_size" not in extra and "chunk_overlap" not in extra
        assert c7.CONFIG_BY_KEY[key].size == 4080


# --------------------------------------------------------------------------- #
# Keys are store names: unambiguous, stable, scratch-prefixed
# --------------------------------------------------------------------------- #
def test_keys_are_legal_and_unambiguous_store_names():
    for key in c7.ALL_CONFIG_KEYS:
        assert re.fullmatch(r"[a-z0-9_]+", key), key
        assert not key.startswith(("_", "-", "+")), key
        assert len(key) < 100, key


def test_a_grid_key_states_both_size_and_overlap_fraction():
    grid_only = [k for k in c7.STAGE1_CONFIG_KEYS if k != "fixed_tok512"]
    for key in grid_only:
        cfg = c7.STAGE1_CONFIG_BY_KEY[key]
        assert str(cfg.size) in key, key
        assert c7.overlap_frac_key(cfg.overlap_frac) in key, key


@pytest.mark.parametrize(
    ("frac", "expected"),
    [(0.0, "ov0pct"), (0.125, "ov12_5pct"), (0.25, "ov25pct"), (0.5, "ov50pct")],
)
def test_overlap_frac_key_spells_percent_unambiguously(frac, expected):
    """``ov12_5pct`` reads as 12.5 percent. ``ov125`` would read as 125 tokens,
    which is the very confusion this grid exists to remove."""
    assert c7.overlap_frac_key(frac) == expected


def test_every_grid_store_name_is_unmistakably_scratch_prefixed():
    """The keys become Qdrant collections and ES indices. Both harnesses'
    teardown guards assert their prefix, so a grid key must not be able to
    produce a name outside it."""
    for key in c7.STAGE1_CONFIG_KEYS:
        assert sfe._store_name(key).startswith("scifact_m7_")
        assert c7._store_name(key).startswith(c7.DEFAULT_PREFIX + "_")
        # ...and cannot be confused with the production corpus.
        assert not sfe._store_name(key).startswith("ragstack")
        assert not c7._store_name(key).startswith("ragstack")


# --------------------------------------------------------------------------- #
# Selection: subsets work, an unknown key fails loudly
# --------------------------------------------------------------------------- #
def test_selecting_the_stage1_group_runs_exactly_the_24():
    got = sfe.select_configs("stage1")
    assert [c.key for c in got] == [
        k for k in c7.ALL_CONFIG_KEYS if k in set(c7.STAGE1_CONFIG_KEYS)
    ]
    assert len(got) == 24, (
        "the stats reference is force-included; it is a cell of the grid, so it "
        "must not add a 25th build"
    )


def test_selecting_a_subset_by_key_runs_that_subset():
    got = sfe.select_configs("fixed_tok1024_ov0pct,semantic_tok2048_ov12_5pct")
    keys = {c.key for c in got}
    # The requested two, plus the force-included statistics reference.
    assert keys == {
        "fixed_tok1024_ov0pct", "semantic_tok2048_ov12_5pct", "fixed_tok512"
    }


def test_selecting_a_single_legacy_key_still_works():
    got = sfe.select_configs("semantic_pooled")
    assert {c.key for c in got} == {"semantic_pooled", "fixed_tok512"}


def test_selection_returns_configs_not_just_keys():
    got = sfe.select_configs("fixed_tok256_ov25pct")
    cfg = next(c for c in got if c.key == "fixed_tok256_ov25pct")
    assert (cfg.kind, cfg.size, cfg.overlap) == ("token_window", 256, 64)


@pytest.mark.parametrize(
    "spec",
    [
        "fixed_tok777_ov12_5pct",           # a plausible-looking non-existent cell
        "fixed_tok512_ov12_5pct",           # the alias's un-taken uniform spelling
        "fixed_tok1024_ov0pct,nonsense",    # one good, one bad
        "stage2",                           # a group that does not exist yet
        "STAGE1",                           # groups are case-sensitive
        "",                                 # selects nothing
        " , ",                              # selects nothing
    ],
)
def test_an_unknown_config_fails_loudly_instead_of_running_everything(spec):
    """The silent failure mode this guards: a typo falling through to "run them
    all". Each of these is a store build measured in GPU-hours, so a wrong set is
    not something to find out from the report afterwards."""
    with pytest.raises(SystemExit):
        sfe.select_configs(spec)


def test_the_refusal_names_the_valid_keys_and_groups():
    with pytest.raises(SystemExit) as exc:
        sfe.select_configs("fixed_tok1024")  # missing the overlap suffix
    message = str(exc.value)
    assert "fixed_tok1024" in message
    assert "stage1" in message
    assert "fixed_tok1024_ov0pct" in message


def test_no_configs_flag_leaves_the_default_run_untouched():
    """An existing invocation that names no configs must run exactly what it ran
    before the grid was added."""
    assert [c.key for c in c7.CONFIGS] == LEGACY_KEYS


# --------------------------------------------------------------------------- #
# The resolved absolute stays visible in labels and emitted metrics
# --------------------------------------------------------------------------- #
def test_every_grid_label_shows_the_resolved_absolute_and_the_fraction():
    for cfg in c7.STAGE1_CONFIGS:
        pct = f"{cfg.overlap_frac * 100:g}%"
        assert pct in cfg.label, f"{cfg.key}: {cfg.label!r} omits {pct}"
        assert str(cfg.overlap) in cfg.label, (
            f"{cfg.key}: {cfg.label!r} omits the resolved {cfg.overlap}"
        )


def test_describe_overlap_shows_both_halves():
    assert (
        c7.describe_overlap(c7.STAGE1_CONFIG_BY_KEY["fixed_tok2048_ov12_5pct"])
        == "256 tok (12.5%)"
    )
    # A config written as a bare absolute has no fraction to show.
    assert c7.describe_overlap(c7.CONFIG_BY_KEY["fixed_char512"]) == "64 char"


def test_emitted_csv_carries_the_fraction_and_the_resolved_absolute(
    tmp_path, monkeypatch
):
    """"Visible in any emitted metrics" — the CSV a stage-1 run leaves behind."""
    monkeypatch.setattr(c7, "CSV_PATH", tmp_path / "out.csv")
    ingest = {
        k: {
            "label": c7.CONFIG_BY_KEY[k].label,
            "overlap_frac": c7.CONFIG_BY_KEY[k].overlap_frac,
            "overlap_tokens": c7.CONFIG_BY_KEY[k].overlap,
            "n_chunks": 10, "chunks_per_doc": 1.0, "median_chars": 1.0,
            "p95_chars": 1.0, "median_tokens": 1.0, "p95_tokens": 1.0,
            "max_tokens_seen": 1, "n_capped": 0, "chunk_time_s": 1.0,
            "ingest_time_s": 1.0, "total_time_s": 2.0,
        }
        for k in c7.CONFIG_KEYS
    }
    zeros = {"recall@1": 0.0, "recall@5": 0.0, "recall@10": 0.0,
             "mrr@10": 0.0, "ndcg@10": 0.0}
    evals = {
        k: {"hybrid": zeros, "reranked_recall@5": 0.0, "reranked_mrr@10": 0.0,
            "mean_rerank_top1": 0.0}
        for k in c7.CONFIG_KEYS
    }
    c7.write_csv(ingest, evals, n_docs=10)

    with (tmp_path / "out.csv").open(encoding="utf-8") as fh:
        rows = {r["config"]: r for r in csv.DictReader(fh)}
    assert rows["fixed_tok512"]["overlap_frac"] == "0.125"
    assert rows["fixed_tok512"]["overlap_tokens"] == "64"
