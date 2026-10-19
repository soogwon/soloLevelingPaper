"""단계별 읽기 가이드(M1-06, #51): 실제 SQLite로 범위 조립·시작·완료·이어하기를 검증한다."""
from contextlib import closing

import pytest

from solo_leveling.application.evidence_qa.errors import ResourceNotFoundError
from solo_leveling.application.reading_guide.service import ReadingGuideService
from solo_leveling.domain.learning_progress import LEARNING_STEPS
from solo_leveling.domain.models import Chunk, LearningContext
from solo_leveling.domain.reading_guide import GOAL_HINTS_KO, STEP_GOALS_KO, STEP_TITLES_KO
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection
from tests.unit.test_learning_persistence import setup_db, publish

SECTIONS = [  # (chunk_id, section_id, pdf_page)
    ('c1', '00-abstract', 1), ('c2', '01-introduction', 1),
    ('c3', '02-method', 2), ('c4', '02-method', 3),
    ('c5', '03-experiments', 4), ('c6', '04-conclusion', 5),
    ('c7', '05-references', 6),
]


class FakeConcepts:
    def key_concepts(self, context_id):
        return {'preview': ['핵심 주장'], 'structure': ['방법 요약']}


@pytest.fixture
def sectioned(setup_db):
    db, chunks = setup_db
    repo.insert_chunks(db, [Chunk(f'c{i}', 'p1', i, f'Text {i}.', pdf_page=1) for i in range(3, 8)])
    with closing(get_connection(db)) as conn, conn:
        for chunk_id, section, page in SECTIONS:
            conn.execute('UPDATE chunks SET section_id=?, pdf_page=? WHERE chunk_id=?', (section, page, chunk_id))
    return db, publish(db, chunks).context_id


def by_step(guide):
    return {s.step: s for s in guide.steps}


def test_every_step_has_title_goal_and_scope(sectioned):
    db, cid = sectioned
    guide = ReadingGuideService(db).get_guide(cid)
    assert [s.step for s in guide.steps] == list(LEARNING_STEPS)
    assert set(STEP_TITLES_KO) == set(STEP_GOALS_KO) == set(LEARNING_STEPS)
    steps = by_step(guide)
    assert all(s.title_ko and s.goal_ko for s in guide.steps)
    assert steps['preview'].section_ids == ('00-abstract', '01-introduction', '04-conclusion')
    assert steps['preview'].pdf_pages == (1, 5)
    assert steps['structure'].section_ids[0] == '00-abstract' and len(steps['structure'].section_ids) == 6
    assert steps['structure'].pdf_pages == (1, 6)
    assert '00-abstract' not in steps['deep_read'].section_ids
    assert '05-references' not in steps['deep_read'].section_ids
    assert steps['deep_read'].section_ids == ('01-introduction', '02-method', '03-experiments', '04-conclusion')
    assert steps['deep_read'].pdf_pages == (1, 5)
    assert steps['review'].section_ids == ('03-experiments', '04-conclusion')
    assert steps['review'].pdf_pages == (4, 5)


def test_new_context_starts_at_first_step(sectioned):
    db, cid = sectioned
    guide = ReadingGuideService(db).get_guide(cid)
    assert guide.current_step == LEARNING_STEPS[0] and guide.progress == 0.0
    assert [s.status for s in guide.steps] == ['current', 'upcoming', 'upcoming', 'upcoming']
    assert guide.resume_position is None
    assert guide.known_concepts == ('어텐션',)


def test_start_and_complete_update_status_and_progress(sectioned):
    db, cid = sectioned
    svc = ReadingGuideService(db)
    guide = svc.complete_step(cid, 'preview')
    assert guide.current_step == 'structure' and guide.progress == 0.25
    assert [s.status for s in guide.steps] == ['done', 'current', 'upcoming', 'upcoming']
    guide = svc.start_step(cid, 'deep_read', {'section_id': '02-method', 'pdf_page': 3})
    assert guide.current_step == 'deep_read'
    assert [s.status for s in guide.steps] == ['done', 'upcoming', 'current', 'upcoming']
    assert guide.last_studied_at is not None


def test_resume_after_reconnect_returns_stopped_step_and_position(sectioned):
    db, cid = sectioned
    ReadingGuideService(db).complete_step(cid, 'preview')
    ReadingGuideService(db).start_step(cid, 'deep_read', {'section_id': '02-method', 'pdf_page': 3})
    guide = ReadingGuideService(db).get_guide(cid)  # 새 서비스 = 재시작 후 조회
    assert guide.current_step == 'deep_read'
    assert guide.resume_position == {'section_id': '02-method', 'pdf_page': 3}
    assert guide.completed_steps == ('preview',)


def test_complete_is_idempotent_and_all_done(sectioned):
    db, cid = sectioned
    svc = ReadingGuideService(db)
    for step in LEARNING_STEPS:
        svc.complete_step(cid, step)
    guide = svc.complete_step(cid, 'review')
    assert guide.progress == 1.0 and guide.current_step == 'review'
    assert all(s.status in ('done', 'current') for s in guide.steps)


def test_invalid_step_and_unknown_context(sectioned):
    db, cid = sectioned
    svc = ReadingGuideService(db)
    with pytest.raises(ValueError):
        svc.start_step(cid, 'cram')
    with pytest.raises(ValueError):
        svc.complete_step(cid, 'cram')
    with pytest.raises(ResourceNotFoundError):
        svc.get_guide('missing')


def test_goal_changes_guidance_text(sectioned):
    db, cid = sectioned
    base = by_step(ReadingGuideService(db).get_guide(cid))['deep_read'].goal_ko
    with closing(get_connection(db)) as conn, conn:
        conn.execute("UPDATE learning_contexts SET goal='implement' WHERE context_id=?", (cid,))
    changed = by_step(ReadingGuideService(db).get_guide(cid))['deep_read'].goal_ko
    assert changed.startswith(base) and GOAL_HINTS_KO['implement']['deep_read'] in changed


def test_key_concepts_come_from_source_and_are_empty_without_it(sectioned):
    db, cid = sectioned
    assert all(s.key_concepts == () for s in ReadingGuideService(db).get_guide(cid).steps)
    steps = by_step(ReadingGuideService(db, FakeConcepts()).get_guide(cid))
    assert steps['preview'].key_concepts == ('핵심 주장',)
    assert steps['structure'].key_concepts == ('방법 요약',)
    assert steps['review'].key_concepts == ()


def test_paper_without_sections_falls_back_to_pages(setup_db):
    db, chunks = setup_db  # c1: 1쪽, c2: 2쪽, section_id 없음
    repo.insert_chunks(db, [Chunk('c3', 'p1', 2, 'Third.', pdf_page=3)])
    cid = publish(db, chunks).context_id
    steps = by_step(ReadingGuideService(db).get_guide(cid))
    assert steps['preview'].section_ids == () and steps['preview'].pdf_pages == (1, 2)
    assert steps['deep_read'].section_ids == () and steps['deep_read'].pdf_pages == (1, 3)


def test_unusual_section_names_still_get_a_scope(setup_db):
    db, chunks = setup_db
    with closing(get_connection(db)) as conn, conn:
        conn.execute("UPDATE chunks SET section_id='01-overview' WHERE chunk_id='c1'")
        conn.execute("UPDATE chunks SET section_id='02-approach' WHERE chunk_id='c2'")
    cid = publish(db, chunks).context_id
    steps = by_step(ReadingGuideService(db).get_guide(cid))
    assert steps['preview'].section_ids == ('01-overview',)   # 이름이 달라도 첫 섹션으로 대신한다
    assert steps['review'].section_ids == ('02-approach',)    # 마지막 섹션으로 대신한다
    assert steps['deep_read'].section_ids == ('01-overview', '02-approach')
