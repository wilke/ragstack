"""``same_record_family`` (#828 PR 5a): a JATS article and its unit records
are one work for the duplicate guard; nothing else is."""
import pytest

from ragstack.ingestion.work_identity import same_record_family


@pytest.mark.parametrize("a, b, expected", [
    ("PMC1", "PMC1#table-1", True),
    ("PMC1#figure-2", "PMC1#table-1", True),
    ("PMC1#table-1", "PMC1#table-1", True),
    ("PMC1", "PMC2#table-1", False),
    ("PMC1", "PMC1", False),             # no unit record: the doc-id rule decides
    ("", "", False),                     # two path-less records are not siblings
    ("", "#x", False),
    ("/data/a#b.pdf", "/data/a", False),  # a file path is never split
    ("/data/PMC1.xml", "PMC1#table-1", False),
    # #975 F2: only the unit grammar jats.py emits is a family member.
    ("PMC1", "PMC1#table-2-part-3", True),
    ("PMC1#figure-1-part-2", "PMC1#table-1", True),
    (".hidden", ".hidden#table-1", True),
    ("journal.pone.0012345", "journal.pone.0012345#figure-1", True),
    ("corpus#0001", "corpus#0002", False),       # JSONL records sharing a DOI
    ("corpus", "corpus#0001", False),
    ("PMC1#supp.pdf", "PMC1", False),            # an uploaded file name
    ("PMC1#supp.pdf", "PMC1#table-1", False),
    ("PMC1#table-x", "PMC1", False),
    ("PMC1#table-1-part-", "PMC1", False),
    ("PMC1#tables-1", "PMC1", False),
    # #975 round 2: a file with no PMC id is keyed on its resolved path
    # (jats.record_key step 3), so a unit root may hold "/".
    ("/corpus/a/paper.xml", "/corpus/a/paper.xml#table-1", True),
    ("/corpus/a/paper.xml#figure-1", "/corpus/a/paper.xml#table-2-part-1", True),
    ("/corpus/a/paper.xml", "/corpus/b/paper.xml#table-1", False),
    ("/corpus/a/paper.xml", "/corpus/a/paper.xml", False),
    ("/corpus/a/paper.pdf#page-1", "/corpus/a/paper.pdf", False),
    ("/corpus/a/notes#x.md", "/corpus/a/notes", False),
])
def test_same_record_family(a, b, expected):
    assert same_record_family(a, b) is expected
    assert same_record_family(b, a) is expected

