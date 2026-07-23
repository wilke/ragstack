"""Query/retrieve Sources surface the chunk's char offsets into the original
document, so the Compare UI can measure cross-chunker passage-span overlap
(same region of a doc, not just same doc_id)."""
from types import SimpleNamespace

from ragstack.api.routers.query import _source_metadata


def _chunk(metadata, start, end):
    return SimpleNamespace(metadata=metadata, start_char=start, end_char=end)


def test_offsets_attached_when_meaningful():
    md = _source_metadata(_chunk({"title": "t"}, 100, 356))
    assert md["title"] == "t"
    assert md["start_char"] == 100 and md["end_char"] == 356


def test_offsets_absent_when_unset():
    # a chunk/store without offsets (0..0) leaves them out rather than emitting 0..0
    md = _source_metadata(_chunk({"title": "t"}, 0, 0))
    assert "start_char" not in md and "end_char" not in md


def test_existing_metadata_offsets_not_overwritten():
    md = _source_metadata(_chunk({"start_char": 5, "end_char": 9}, 100, 356))
    assert md["start_char"] == 5 and md["end_char"] == 9  # setdefault: don't clobber


def test_metadata_is_copied_not_mutated():
    original = {"title": "t"}
    _source_metadata(_chunk(original, 1, 2))
    assert "start_char" not in original  # source dict untouched
