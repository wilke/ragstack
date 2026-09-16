"""``_safe_upload_name`` — the upload filename must be safe AND ASCII.

Why ASCII and not just traversal-free: BV-BRC's Workspace cannot address a path
with a non-ASCII component. ``get_download_url`` raises
``[-32603] Can't escape \\x{2010}, try uri_escape_utf8() instead``, so GoWe's
server-side pre-staging fails and the ingest dies at ``extract`` with an error
that names neither the offending file nor the character (GoWe#267).

That is not hypothetical — it is how submission ``sub_b189807d`` failed on the
hackathon tenant: one source PDF carried a U+2010 HYPHEN in its title, which is
ordinary in publisher-generated filenames. Folding at upload time means nothing
downstream ever sees the non-ASCII name.
"""
from __future__ import annotations

import pytest

from ragstack.api.routers.documents import _safe_upload_name

FALLBACK = "upload_0.pdf"


def safe(raw: str | None) -> str:
    return _safe_upload_name(raw, fallback=FALLBACK, kind="pdf")


# The filename that actually broke a live submission.
FEBS = (
    "The FEBS Journal - 2021 - Pralow - "
    "Comprehensive N‐glycosylation analysis.pdf"
)


def test_the_filename_that_broke_a_live_submission_is_now_ascii() -> None:
    out = safe(FEBS)
    assert out.isascii()
    assert "‐" not in out
    # readable, not mangled: the hyphen is a hyphen, the word is intact
    assert "N-glycosylation" in out
    assert out.endswith(".pdf")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # every dash publishers use, mapped to ASCII '-'
        ("a‐b.pdf", "a-b.pdf"),
        ("a‑b.pdf", "a-b.pdf"),
        ("a–b.pdf", "a-b.pdf"),
        ("a—b.pdf", "a-b.pdf"),
        ("a−b.pdf", "a-b.pdf"),
        # curly quotes
        ("“q”.pdf", "'q'.pdf"),
        ("it’s.pdf", "it's.pdf"),
        # accents decompose rather than vanish
        ("Café résumé.pdf", "Cafe resume.pdf"),
        # NBSP and friends become a plain space
        ("a b.pdf", "a b.pdf"),
        ("a b.pdf", "a b.pdf"),
        # zero-width characters are deleted, not turned into '_'
        ("a​b.pdf", "ab.pdf"),
        ("a﻿b.pdf", "ab.pdf"),
        ("a­b.pdf", "ab.pdf"),
        # plain names are untouched
        ("plain.pdf", "plain.pdf"),
    ],
)
def test_typographic_characters_fold_to_readable_ascii(raw: str, expected: str) -> None:
    assert safe(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # '#' matters most: item ids are "<source>#<index>", so a '#' in the
        # name would split the id in the wrong place.
        ("report#2.pdf", "report_2.pdf"),
        ("100%done.pdf", "100_done.pdf"),
        ("what?.pdf", "what_.pdf"),
    ],
)
def test_uri_hostile_ascii_is_replaced(raw: str, expected: str) -> None:
    assert safe(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["../../etc/passwd", "/abs/path/x.pdf", "a\\b\\c.pdf", "dir/sub/name.pdf"],
)
def test_traversal_and_directory_parts_are_still_dropped(raw: str) -> None:
    out = safe(raw)
    assert "/" not in out
    assert "\\" not in out
    assert not out.startswith("..")


@pytest.mark.parametrize("raw", [None, "", ".", "..", "   ", "\x00"])
def test_unusable_names_fall_back(raw: str | None) -> None:
    assert safe(raw) == FALLBACK


def test_a_name_of_only_separators_falls_back() -> None:
    # folds to "..." / "___" and carries no information -- must not be written
    assert safe("…") == FALLBACK
    assert safe("---") == FALLBACK


def test_lossy_folds_stay_distinct_and_are_stable() -> None:
    """Two names differing only outside ASCII must not collide.

    Without this, both become "__.pdf" and the second upload 409s against a
    filename the user never chose -- a confusing refusal rather than a clear one.
    """
    a, b = safe("中文A.pdf"), safe("中文B.pdf")
    assert a != b
    assert a.isascii() and b.isascii()
    # stable across retries, so a resubmission 409s honestly instead of
    # silently writing a second copy under a new name
    assert a == safe("中文A.pdf")


def test_a_purely_non_ascii_name_survives_as_something_addressable() -> None:
    out = safe("中文文件")
    assert out.isascii()
    assert out.endswith(".pdf")
    assert out != FALLBACK  # distinct files keep distinct names


def test_the_kind_suffix_is_forced_when_absent() -> None:
    assert safe("noext").endswith(".pdf")
    assert safe("already.pdf") == "already.pdf"


@pytest.mark.parametrize(
    "raw",
    [
        FEBS,
        "中文.pdf",
        "क्ष.pdf",
        "\U0001f600 emoji.pdf",
        "mixed – é ​   #%? .pdf",
    ],
)
def test_the_result_is_always_ascii(raw: str) -> None:
    """The invariant the Workspace actually needs — no exceptions."""
    assert safe(raw).isascii()
