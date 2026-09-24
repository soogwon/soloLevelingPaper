"""실제 SQLite로 트랜잭션·리비전·재시작 후 데이터 보존을 검증한다."""
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from solo_leveling.domain.models import (
    Chunk, Paper, PaperVersion, ParseRevision, ProcessingJob, EmbeddingSet, LearningContext, Evidence,
)
from solo_leveling.domain.translation import TranslationSettings
from solo_leveling.application.translation.service import TranslationService
from solo_leveling.infrastructure.translation.fake_provider import FakeTranslationProvider
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import init_db, get_connection, SCHEMA_SQL


@pytest.fixture
def setup_db(tmp_path):
    db = str(tmp_path / 'data.sqlite')
    init_db(db)
    repo.insert_paper(db, Paper('paper'))
    repo.insert_version(db, PaperVersion('v1', 'paper', 'hash', '/private/paper.pdf', r'C:\private\paper.pdf'))
    repo.insert_parse_revision(db, ParseRevision('p1', 'v1', 2))
    chunks = [Chunk('c1', 'p1', 0, 'First.', pdf_page=1), Chunk('c2', 'p1', 1, 'Second.', pdf_page=2)]
    repo.insert_chunks(db, chunks)
    return db, chunks


def make_batch(chunks, revision='t1', translations=None, failures=()):
    provider = FakeTranslationProvider(translations or {'First.': '첫째.', 'Second.': '둘째.'}, failures)
    return TranslationService(provider, lambda: revision).translate(
        chunks, TranslationSettings('fake', 'fixture', 'prompt1'))


def publish(db, chunks):
    repo.save_translation_batch(db, make_batch(chunks))
    repo.claim_ingestion_job(db, ProcessingJob('job', 'v1'))
    repo.publish_search_index(db, 'v1', 'p1', EmbeddingSet('idx', 't1', 'fake', 2), 'job', 2, [])
    context = LearningContext('ctx', 'v1', 'p1', 't1', known_concepts=['어텐션'], embedding_set_id='idx')
    repo.create_learning_context(db, context)
    return context


def test_migration_preserves_legacy_rows_and_is_repeatable(tmp_path):
    db = str(tmp_path / 'legacy.sqlite')
    legacy = SCHEMA_SQL.split('CREATE TABLE IF NOT EXISTS translation_results')[0]
    legacy = legacy.replace('    prompt_version TEXT,\n', '').replace('    target_language TEXT,\n', '')
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript(legacy)
    repo.insert_paper(db, Paper('legacy-paper'))
    init_db(db)
    init_db(db)
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT paper_id FROM papers').fetchone()[0] == 'legacy-paper'
        columns = {r['name'] for r in conn.execute('PRAGMA table_info(translation_revisions)')}
        assert {'prompt_version', 'target_language'} <= columns
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_translation_persistence_and_idempotency(setup_db):
    db, chunks = setup_db
    batch = make_batch(chunks)
    repo.save_translation_batch(db, batch)
    repo.save_translation_batch(db, batch)
    stored = repo.get_translated_chunks(db, 'p1', 't1')
    assert [c.text for c in stored] == ['첫째.', '둘째.']
    assert [c.original_text for c in stored] == ['First.', 'Second.']
    assert repo.get_translation_metadata(db, 'p1')['prompt_version'] == 'prompt1'
    with pytest.raises(ValueError):
        repo.get_translated_chunks(db, 'other', 't1')


def test_failure_retry_preserves_successes(setup_db):
    db, chunks = setup_db
    repo.save_translation_batch(db, make_batch(chunks, failures=('c2',)))
    assert repo.get_translation_failures(db, 't1') == [{'chunk_id': 'c2', 'failure_code': 'provider_unavailable'}]
    repo.save_translation_batch(db, make_batch([chunks[1]]))
    assert len(repo.get_translated_chunks(db, 'p1', 't1')) == 2
    assert repo.get_translation_failures(db, 't1') == []
    with pytest.raises(ValueError):
        repo.save_translation_batch(db, make_batch(chunks, translations={'First.': '변경', 'Second.': '둘째.'}))
    with pytest.raises(ValueError):
        repo.save_translation_batch(db, make_batch(chunks, revision='t2'))
    assert repo.get_chunk(db, 'c1').text == '첫째.'


def test_translation_batch_rolls_back_every_write(setup_db):
    db, chunks = setup_db
    bad = make_batch([chunks[0], replace(chunks[1], original_text='Mismatch.')],
                     translations={'First.': '첫째.', 'Mismatch.': '불일치'})
    with pytest.raises(ValueError):
        repo.save_translation_batch(db, bad)
    assert repo.get_chunk(db, 'c1').text is None
    assert repo.get_translation_metadata(db, 'p1') is None


def test_context_and_evidence_survive_connections_and_init(setup_db):
    db, chunks = setup_db
    context = publish(db, chunks)
    repo.save_evidences(db, 'ctx', [Evidence('e1', 'c1', '첫째', 'First')])
    init_db(db)
    assert repo.get_learning_context(db, 'ctx') == context
    result = repo.get_evidences(db, 'ctx', ['e1'])
    detail = result.evidence[0]
    assert (detail.file_display_name, detail.pdf_page, detail.translation_revision_id) == ('paper.pdf', 1, 't1')
    assert detail.quote_ko == '첫째'
    assert repo.get_search_index(db, 'v1', 'wrong', 't1') is None


def test_context_requires_matching_published_index(setup_db):
    db, chunks = setup_db
    invalid = LearningContext('early', 'v1', 'p1', 't1', embedding_set_id='idx')
    with pytest.raises(ValueError):
        repo.create_learning_context(db, invalid)
    context = publish(db, chunks)
    for field in ('version_id', 'parse_revision_id', 'translation_revision_id', 'embedding_set_id'):
        with pytest.raises(ValueError):
            repo.create_learning_context(db, replace(context, context_id='bad', **{field: 'other'}))


def test_evidence_scope_quotes_and_batch_atomicity(setup_db):
    db, chunks = setup_db
    context = publish(db, chunks)
    repo.create_learning_context(db, replace(context, context_id='ctx2'))
    repo.save_evidences(db, 'ctx', [Evidence('e1', 'c1', '첫째', 'First')])
    with pytest.raises(ValueError):
        repo.get_evidences(db, 'ctx2', ['e1'])
    repo.insert_parse_revision(db, ParseRevision('p2', 'v1', 1))
    repo.insert_chunks(db, [Chunk('outside', 'p2', 0, 'First.')])
    for evidence in (Evidence('bad', 'outside', None, 'First'), Evidence('bad', 'c1', '없는 번역', 'First'),
                     Evidence('bad', 'c1', None, 'invented')):
        with pytest.raises(ValueError):
            repo.save_evidences(db, 'ctx', [Evidence('rollback', 'c2', None, 'Second'), evidence])
        with pytest.raises(ValueError):
            repo.get_evidences(db, 'ctx', ['rollback'])
    with pytest.raises(ValueError):
        repo.get_evidences(db, 'ctx', ['e1', 'missing'])


def test_published_translation_cannot_fill_new_gaps(setup_db):
    db, chunks = setup_db
    repo.save_translation_batch(db, make_batch(chunks, failures=('c2',)))
    repo.claim_ingestion_job(db, ProcessingJob('job', 'v1'))
    repo.publish_search_index(db, 'v1', 'p1', EmbeddingSet('idx', 't1', 'fake', 2), 'job', 1, [])
    with pytest.raises(ValueError):
        repo.save_translation_batch(db, make_batch([chunks[1]]))
    assert repo.get_chunk(db, 'c2').text is None


def test_publish_count_and_job_mismatch_roll_back(setup_db):
    db, chunks = setup_db
    repo.save_translation_batch(db, make_batch(chunks))
    repo.claim_ingestion_job(db, ProcessingJob('job', 'v1'))
    with pytest.raises(ValueError):
        repo.claim_ingestion_job(db, ProcessingJob('parallel', 'v1'))
    with pytest.raises(ValueError):
        repo.publish_search_index(db, 'v1', 'p1', EmbeddingSet('idx', 't1', 'fake', 2), 'job', 1, [])
    assert repo.get_search_index(db, 'v1') is None
    assert repo.get_job(db, 'job').status.value == 'processing'
