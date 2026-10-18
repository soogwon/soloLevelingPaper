"""근거 연결형 요약(M1-05, #50)의 검증·저장·조회를 실제 SQLite로 검증한다."""
import sqlite3
from contextlib import closing

import pytest

from solo_leveling.domain.models import Chunk, Evidence
from solo_leveling.domain.paper_summary import (
    PAPER_SCOPE, SUMMARY_KINDS, SummaryItem, SummaryValidationError, split_supported,
)
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection
from tests.unit.test_learning_persistence import setup_db, publish


@pytest.fixture
def ctx(setup_db):
    db, chunks = setup_db
    context = publish(db, chunks)
    repo.save_evidences(db, context.context_id, [
        Evidence('e1', 'c1', '첫째.', 'First.'),
        Evidence('e2', 'c2', '둘째.', 'Second.'),
    ])
    return db, context.context_id


def items():
    return [SummaryItem('claim', '핵심 주장', ('e1',)),
            SummaryItem('result', '결과 요약', ['e1', 'e2'])]


def test_item_requires_evidence_text_and_known_kind():
    assert set(SUMMARY_KINDS) == {'claim', 'method', 'result', 'limitation'}
    with pytest.raises(SummaryValidationError):
        SummaryItem('claim', '근거 없음', ())
    with pytest.raises(SummaryValidationError):
        SummaryItem('claim', '   ', ('e1',))
    with pytest.raises(SummaryValidationError):
        SummaryItem('opinion', '종류 오류', ('e1',))


def test_split_supported_separates_unknown_evidence():
    ok, bad = split_supported(items() + [SummaryItem('method', '엉뚱한 근거', ('zzz',))], ['e1', 'e2'])
    assert [i.kind for i in ok] == ['claim', 'result']
    assert [i.kind for i in bad] == ['method']


def test_save_and_get_round_trip_keeps_order(ctx):
    db, cid = ctx
    saved = repo.save_summary(db, cid, PAPER_SCOPE, items())
    got = repo.get_summary(db, cid)
    assert got.summary_id == saved.summary_id
    assert [(i.kind, i.text, i.evidence_ids) for i in got.items] == [
        ('claim', '핵심 주장', ('e1',)), ('result', '결과 요약', ('e1', 'e2'))]
    assert repo.list_summary_scopes(db, cid) == [PAPER_SCOPE]


def test_missing_summary_returns_none(ctx):
    db, cid = ctx
    assert repo.get_summary(db, cid) is None
    assert repo.list_summary_scopes(db, cid) == []


def test_unknown_evidence_saves_nothing(ctx):
    db, cid = ctx
    with pytest.raises(SummaryValidationError, match='zzz'):
        repo.save_summary(db, cid, PAPER_SCOPE, items() + [SummaryItem('method', '근거 위조', ('zzz',))])
    assert repo.get_summary(db, cid) is None
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM summary_items').fetchone()[0] == 0


def test_empty_items_rejected(ctx):
    db, cid = ctx
    with pytest.raises(SummaryValidationError):
        repo.save_summary(db, cid, PAPER_SCOPE, [])


def test_evidence_of_another_context_is_rejected(ctx):
    db, cid = ctx
    from solo_leveling.domain.models import LearningContext
    other = LearningContext('ctx2', 'v1', 'p1', 't1', known_concepts=['다른 개념'], embedding_set_id='idx')
    repo.create_learning_context(db, other)
    with pytest.raises(SummaryValidationError):
        repo.save_summary(db, 'ctx2', PAPER_SCOPE, [SummaryItem('claim', '남의 근거', ('e1',))])


def test_resave_replaces_same_scope(ctx):
    db, cid = ctx
    repo.save_summary(db, cid, PAPER_SCOPE, items())
    repo.save_summary(db, cid, PAPER_SCOPE, [SummaryItem('limitation', '한계', ('e2',))])
    got = repo.get_summary(db, cid)
    assert [i.kind for i in got.items] == ['limitation']
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM summaries').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM summary_items').fetchone()[0] == 1


def test_section_scope_must_exist_in_paper(setup_db):
    db, chunks = setup_db
    with closing(get_connection(db)) as conn, conn:
        conn.execute("UPDATE chunks SET section_id='01-intro' WHERE chunk_id='c1'")
    context = publish(db, chunks)
    repo.save_evidences(db, context.context_id, [Evidence('e1', 'c1', '첫째.', 'First.')])
    repo.save_summary(db, context.context_id, '01-intro', [SummaryItem('claim', '서론 요약', ('e1',))])
    assert repo.list_summary_scopes(db, context.context_id) == ['01-intro']
    with pytest.raises(SummaryValidationError, match='섹션'):
        repo.save_summary(db, context.context_id, '99-none', [SummaryItem('claim', '없는 섹션', ('e1',))])


def test_summary_items_lead_to_source_text_and_page(ctx):
    db, cid = ctx
    summary = repo.save_summary(db, cid, PAPER_SCOPE, items())
    detail = repo.get_summary_evidence(db, summary).evidence
    assert [(d.evidence_id, d.quote_original, d.pdf_page) for d in detail] == [
        ('e1', 'First.', 1), ('e2', 'Second.', 2)]


def test_summaries_persist_after_reconnect(ctx):
    db, cid = ctx
    repo.save_summary(db, cid, PAPER_SCOPE, items())
    assert repo.get_summary(db, cid) is not None  # 새 연결로 다시 읽는다
