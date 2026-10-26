"""읽기 가이드의 핵심 개념이 저장된 근거 연결형 요약(M1-05)에서 오는지 검증한다."""
from solo_leveling.application.reading_guide.service import ReadingGuideService
from solo_leveling.application.reading_guide.summary_concepts import SummaryKeyConceptSource
from solo_leveling.domain.models import Evidence
from solo_leveling.domain.paper_summary import PAPER_SCOPE, SummaryItem
from solo_leveling.infrastructure.database import repository as repo
from tests.unit.test_learning_persistence import setup_db, publish


def prepare(setup_db, items):
    db, chunks = setup_db
    cid = publish(db, chunks).context_id
    repo.save_evidences(db, cid, [Evidence('e1', 'c1', '첫째.', 'First.'), Evidence('e2', 'c2', '둘째.', 'Second.')])
    if items:
        repo.save_summary(db, cid, PAPER_SCOPE, items)
    return db, cid


def by_step(guide):
    return {s.step: s for s in guide.steps}


def test_guide_uses_saved_summary_items_per_step(setup_db):
    db, cid = prepare(setup_db, [
        SummaryItem('claim', '핵심 주장', ('e1',)),
        SummaryItem('method', '사용한 방법', ('e1',)),
        SummaryItem('result', '실험 결과', ('e2',)),
        SummaryItem('limitation', '명시된 한계', ('e2',)),
    ])
    steps = by_step(ReadingGuideService(db).get_guide(cid))
    assert steps['preview'].key_concepts == ('핵심 주장',)
    assert steps['structure'].key_concepts == ('사용한 방법',)
    assert steps['deep_read'].key_concepts == ('실험 결과',)
    assert steps['review'].key_concepts == ('명시된 한계',)


def test_guide_without_summary_has_empty_concepts_and_creates_none(setup_db):
    db, cid = prepare(setup_db, [])
    guide = ReadingGuideService(db).get_guide(cid)
    assert all(s.key_concepts == () for s in guide.steps)
    assert repo.get_summary(db, cid) is None  # 가이드가 요약을 만들지 않는다


def test_steps_without_matching_items_stay_empty(setup_db):
    db, cid = prepare(setup_db, [SummaryItem('claim', '주장만 있음', ('e1',))])
    steps = by_step(ReadingGuideService(db).get_guide(cid))
    assert steps['preview'].key_concepts == ('주장만 있음',)
    assert steps['review'].key_concepts == ()


def test_each_step_is_limited_to_max_items(setup_db):
    db, cid = prepare(setup_db, [SummaryItem('result', f'결과 {i}', ('e1',)) for i in range(4)])
    limited = ReadingGuideService(db, SummaryKeyConceptSource(db, max_per_step=2)).get_guide(cid)
    assert by_step(limited)['deep_read'].key_concepts == ('결과 0', '결과 1')
    default = ReadingGuideService(db).get_guide(cid)
    assert len(by_step(default)['deep_read'].key_concepts) == 4


def test_section_summaries_do_not_leak_into_guide(setup_db):
    db, chunks = setup_db
    from contextlib import closing
    from solo_leveling.infrastructure.database.schema import get_connection
    with closing(get_connection(db)) as conn, conn:
        conn.execute("UPDATE chunks SET section_id='01-a' WHERE chunk_id='c1'")
    cid = publish(db, chunks).context_id
    repo.save_evidences(db, cid, [Evidence('e1', 'c1', '첫째.', 'First.')])
    repo.save_summary(db, cid, '01-a', [SummaryItem('claim', '섹션 요약', ('e1',))])
    guide = ReadingGuideService(db).get_guide(cid)
    assert all(s.key_concepts == () for s in guide.steps)  # 전체(paper) 요약만 사용한다
