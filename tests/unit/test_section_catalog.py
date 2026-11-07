"""파싱 리비전별 섹션 카탈로그 저장·조회 계약."""
import pytest

from solo_leveling.application.evidence_qa.errors import DataIntegrityError, ResourceNotFoundError
from solo_leveling.domain.models import Paper, PaperSection, PaperVersion, ParseRevision
from solo_leveling.infrastructure.database.repository import (
    get_section_catalog, insert_paper, insert_parse_revision, insert_sections, insert_version,
)
from solo_leveling.infrastructure.database.schema import init_db


def _database(tmp_path):
    db = str(tmp_path / "sections.db")
    init_db(db)
    insert_paper(db, Paper("paper"))
    insert_version(db, PaperVersion("version", "paper", "hash", "paper.pdf", "paper.pdf"))
    insert_parse_revision(db, ParseRevision("revision", "version", 4))
    return db


def test_catalog_is_scoped_and_ordered_by_parse_revision(tmp_path):
    db = _database(tmp_path)
    insert_sections(db, [
        PaperSection("s2", "revision", "Method", 2, 1, 3, 4),
        PaperSection("s1", "revision", "Introduction", 1, 1, 1, 2),
    ])
    assert [section.title for section in get_section_catalog(db, "version", "revision")] == [
        "Introduction", "Method",
    ]


def test_no_detected_sections_is_distinct_from_missing_or_mismatched_revision(tmp_path):
    db = _database(tmp_path)
    assert get_section_catalog(db, "version", "revision") == []
    with pytest.raises(ResourceNotFoundError):
        get_section_catalog(db, "version", "missing")

    insert_version(db, PaperVersion("other-version", "paper", "other-hash", "other.pdf", "other.pdf"))
    insert_parse_revision(db, ParseRevision("other-revision", "other-version", 1))
    with pytest.raises(DataIntegrityError):
        get_section_catalog(db, "version", "other-revision")


def test_catalog_rejects_invalid_page_range_and_cross_revision_parent(tmp_path):
    db = _database(tmp_path)
    with pytest.raises(ValueError):
        insert_sections(db, [PaperSection("s1", "revision", "Too far", 1, 1, 1, 5)])
    with pytest.raises(ValueError):
        insert_sections(db, [PaperSection("s1", "revision", "Child", 1, 2, 1, 1, "unknown")])


@pytest.mark.parametrize("parents", [
    {"a": "a"},
    {"a": "b", "b": "a"},
    {"a": "b", "b": "c", "c": "a"},
])
def test_cycle_rejects_entire_batch(tmp_path, parents):
    db = _database(tmp_path)
    insert_sections(db, [PaperSection("existing", "revision", "Existing", 1, 1, 1, 1)])
    batch = [PaperSection("root", "revision", "Root", 2, 1, 1, 1)]
    batch.extend(PaperSection(key, "revision", key, ordinal, 2, 1, 1, parent)
                 for ordinal, (key, parent) in enumerate(parents.items(), 3))
    with pytest.raises(ValueError, match="cycle"):
        insert_sections(db, batch)
    assert [s.section_id for s in get_section_catalog(db, "version", "revision")] == ["existing"]


def test_valid_tree_accepts_children_before_parents(tmp_path):
    db = _database(tmp_path)
    sections = [
        PaperSection("root", "revision", "Root", 1, 1, 1, 4),
        PaperSection("branch", "revision", "Branch", 2, 2, 1, 3, "root"),
        PaperSection("leaf", "revision", "Leaf", 3, 3, 1, 2, "branch"),
        PaperSection("sibling", "revision", "Sibling", 4, 2, 4, 4, "root"),
    ]
    insert_sections(db, sections[::-1])
    assert get_section_catalog(db, "version", "revision") == sections
