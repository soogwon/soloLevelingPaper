"""키워드·중요 문장의 원문 출처 및 저장 수명주기를 검증한다."""
import pytest
import sqlite3

from tests.unit.test_learning_persistence import publish, setup_db
from solo_leveling.domain.paper_insight import (
    CONTENT_EXTRACTED, CONTENT_GENERATED, KIND_IMPORTANT_SENTENCE, KIND_KEYWORD,
    PRODUCER_EXTRACTOR, PRODUCER_MODEL, PaperInsightInput, PaperInsightValidationError,
)
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.application.evidence_qa.errors import DataIntegrityError, ResourceNotFoundError


def item(kind=KIND_KEYWORD, text='First', *, chunk_id='c1', start=0, end=5,
         quote='First', content=CONTENT_EXTRACTED, producer=PRODUCER_EXTRACTOR,
         translation_revision_id=None):
    return PaperInsightInput(kind, text, content, producer, chunk_id, start, end, quote,
                             translation_revision_id)


def test_save_and_get_preserve_order_kind_and_exact_source(setup_db):
    db, chunks = setup_db
    publish(db, chunks)
    saved = repo.save_paper_insights(db, 'ctx', 'paper', [
        item(),
        item(KIND_IMPORTANT_SENTENCE, 'Second.', chunk_id='c2', start=0, end=7, quote='Second.'),
        item(KIND_KEYWORD, '핵심 개념', content=CONTENT_GENERATED, producer=PRODUCER_MODEL,
             translation_revision_id='t1'),
    ])
    loaded = repo.get_paper_insights(db, 'ctx')
    assert loaded.collection_id == saved.collection_id
    assert [entry.input.kind for entry in loaded.items] == [
        KIND_KEYWORD, KIND_IMPORTANT_SENTENCE, KIND_KEYWORD,
    ]
    assert loaded.items[0].input.quote_original == 'First'
    assert loaded.items[2].input.content_kind == CONTENT_GENERATED
    assert loaded.items[2].input.translation_revision_id == 't1'


def test_missing_collection_is_not_generated_and_partial_collection_is_allowed(setup_db):
    db, chunks = setup_db
    publish(db, chunks)
    assert repo.get_paper_insights(db, 'ctx') is None
    saved = repo.save_paper_insights(db, 'ctx', 'paper', [item()])
    assert [entry.input.kind for entry in saved.items] == [KIND_KEYWORD]
    with pytest.raises(ResourceNotFoundError):
        repo.get_paper_insights(db, 'missing')
    with pytest.raises(ResourceNotFoundError):
        repo.get_paper_insights(db, 'ctx', 'missing-section')


@pytest.mark.parametrize('bad', [
    lambda: item(start=1, end=6, quote='First'),
    lambda: item(chunk_id='missing'),
    lambda: item(translation_revision_id='wrong'),
])
def test_invalid_provenance_is_rejected_without_replacing_existing_collection(setup_db, bad):
    db, chunks = setup_db
    publish(db, chunks)
    original = repo.save_paper_insights(db, 'ctx', 'paper', [item()])
    with pytest.raises(PaperInsightValidationError):
        repo.save_paper_insights(db, 'ctx', 'paper', [bad()])
    assert repo.get_paper_insights(db, 'ctx').collection_id == original.collection_id


def test_extracted_and_generated_content_are_not_interchangeable():
    with pytest.raises(PaperInsightValidationError):
        item(KIND_IMPORTANT_SENTENCE, '해석 문장', quote='First').__post_init__()
    with pytest.raises(PaperInsightValidationError):
        item(content=CONTENT_GENERATED, producer=PRODUCER_EXTRACTOR)


def test_getter_fails_closed_when_saved_source_no_longer_matches(setup_db):
    db, chunks = setup_db
    publish(db, chunks)
    repo.save_paper_insights(db, 'ctx', 'paper', [item()])
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE chunks SET original_text='Changed.' WHERE chunk_id='c1'")
    with pytest.raises(DataIntegrityError):
        repo.get_paper_insights(db, 'ctx')


def test_previous_schema_keeps_sources_and_unknown_version(setup_db):
    from solo_leveling.infrastructure.database.schema import init_db
    db, chunks = setup_db
    publish(db, chunks)
    saved = repo.save_paper_insights(db, 'ctx', 'paper', [item()])
    with sqlite3.connect(db) as conn:
        conn.execute('ALTER TABLE paper_insights DROP COLUMN producer_version')
    assert repo.get_paper_insights(db, 'ctx') == saved
    init_db(db)
    assert repo.get_paper_insights(db, 'ctx') == saved


def test_conditional_save_is_atomic_and_legacy_replacement_still_works(setup_db):
    from pathlib import Path
    from solo_leveling.domain.paper_insight import PaperInsightConflictError
    db, chunks = setup_db
    publish(db, chunks)
    first = repo.save_paper_insights(db, 'ctx', 'paper', [item()], expected_collection_id=None,
                                   expected_context=('v1', 'p1', 't1', 'idx'))
    before = Path(db).read_bytes()
    for expected in (None, 'stale-collection'):
        with pytest.raises(PaperInsightConflictError):
            repo.save_paper_insights(db, 'ctx', 'paper', [item()], expected_collection_id=expected)
        assert Path(db).read_bytes() == before
    with pytest.raises(DataIntegrityError):
        repo.save_paper_insights(db, 'ctx', 'paper', [item()], expected_collection_id=first.collection_id,
                                expected_context=('wrong', 'p1', 't1', 'idx'))
    assert Path(db).read_bytes() == before
    updated = repo.save_paper_insights(db, 'ctx', 'paper', [item()], expected_collection_id=first.collection_id)
    assert updated.collection_id != first.collection_id
    legacy = repo.save_paper_insights(db, 'ctx', 'paper', [item()])
    assert repo.get_paper_insights(db, 'ctx') == legacy


def test_concurrent_create_only_saves_preserve_one_complete_collection(setup_db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from solo_leveling.domain.paper_insight import PaperInsightConflictError
    db, chunks = setup_db
    publish(db, chunks)
    barrier = Barrier(2)

    def save_once():
        barrier.wait(timeout=5)
        try:
            return repo.save_paper_insights(db, 'ctx', 'paper', [item()], expected_collection_id=None)
        except PaperInsightConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: save_once(), range(2)))
    saved = [result for result in results if result is not None]
    assert len(saved) == 1
    assert repo.get_paper_insights(db, 'ctx') == saved[0]
