"""요약 준비는 원격 고정 판본을 모두 읽으며 다른 계정·삭제 논문은 숨긴다."""
# ruff: noqa: F811
import os
from uuid import uuid4

import pytest

from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.integration.test_remote_retrieval_postgres import seed
from solo_leveling.application.summary.remote_contract import RemoteSummaryError
from solo_leveling.interfaces.http.remote_summary_sources import PostgresSummaryPreparation

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def prep(pool):
    return PostgresSummaryPreparation(pool, provider='synthetic', model='fixed', prompt_version='v1',
                                      language='ko', budget_policy_version='summary-budget-v1')


def test_full_fixed_input_survives_republication_and_restart(pool, db, stores):
    paper, revisions, binding = seed(db, stores, pool, 'alice')
    request = prep(pool).prepare('alice', binding.context_id)
    fixed, sources, raw = request.unpack()
    assert fixed == binding
    assert len(sources) == raw['total_scope_chunks'] == 4
    assert tuple(s.chunk_id for s in sources) == tuple(revisions[3])
    seed(db, stores, pool, 'alice', paper=paper, texts=('New original.', '새 번역.'))
    assert prep(pool).prepare('alice', binding.context_id).input_hash == request.input_hash
    assert db('SELECT count(*) n FROM server_remote_executions')[0]['n'] == 0
    assert db('SELECT count(*) n FROM server_remote_consents')[0]['n'] == 0


def test_owner_deleted_and_missing_context_are_not_found(pool, db, stores):
    paper, _, binding = seed(db, stores, pool, 'alice')
    store = prep(pool)
    assert store.prepare('bob', binding.context_id) is None
    assert store.prepare('alice', str(uuid4())) is None
    db('UPDATE server_papers SET deleted_at=clock_timestamp() WHERE paper_id=%s', (paper,))
    assert store.prepare('alice', binding.context_id) is None


def test_unknown_section_is_empty_not_whole_paper_fallback(pool, db, stores):
    _, _, binding = seed(db, stores, pool, 'alice')
    with pytest.raises(RemoteSummaryError, match='^REMOTE_SUMMARY_SOURCE_EMPTY$'):
        prep(pool).prepare('alice', binding.context_id, scope='section', section_id='missing')


def test_section_reads_only_its_full_scope(pool, db, stores):
    _, _, binding = seed(db, stores, pool, 'alice')
    request = prep(pool).prepare('alice', binding.context_id, scope='section', section_id='s4')
    _, sources, raw = request.unpack()
    assert len(sources) == raw['total_scope_chunks'] == 3
    assert all(source.section_id == 's4' for source in sources)


@pytest.mark.parametrize('status', ['original_only', 'translation_not_required', 'reference_content'])
def test_nontranslated_text_is_not_mislabelled_translation(pool, db, stores, monkeypatch, status):
    from tests.integration import test_remote_retrieval_postgres as fixtures
    original_ready = fixtures.mark_ready

    def ready(db, parse, translation, embedding):
        db('UPDATE server_translation_chunks SET translation_status=%s WHERE translation_revision_id=%s',
           (status, translation))
        original_ready(db, parse, translation, embedding)

    monkeypatch.setattr(fixtures, 'mark_ready', ready)
    _, _, binding = seed(db, stores, pool, 'alice')
    _, sources, _ = prep(pool).prepare('alice', binding.context_id).unpack()
    assert all(s.translated_text is None and s.original_text for s in sources)
