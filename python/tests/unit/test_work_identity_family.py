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
])
def test_same_record_family(a, b, expected):
    assert same_record_family(a, b) is expected
    assert same_record_family(b, a) is expected
