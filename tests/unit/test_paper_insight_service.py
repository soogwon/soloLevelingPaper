"""원문 선정·출처·명시 저장과 기존 조회 무변경을 합성 DB에서 검증한다."""
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest

from solo_leveling.application.evidence_qa.errors import DataIntegrityError, ResourceNotFoundError
from solo_leveling.application.paper_insights.service import ExtractionPolicy, PaperInsightService
from solo_leveling.domain.models import Chunk, PaperSection, ParseRevision
from solo_leveling.domain.paper_insight import PaperInsightConflictError, PaperInsightValidationError
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.retrieval.paper_detail import SQLitePaperDetailReader
from solo_leveling.infrastructure.retrieval.paper_insights import SQLiteInsightStore
from solo_leveling.interfaces.cli.extract_paper_insights import main
from tests.unit.test_learning_persistence import setup_db, publish

FIRST = 'Attention compares queries with keys to select relevant values. Attention combines values through weighted sums.'
SECOND = 'Networks use attention to connect distant positions. Networks support parallel computation across sequence positions.'


@pytest.fixture
def source(setup_db):
    db, chunks = setup_db
    publish(db, chunks)
    with closing(sqlite3.connect(db)) as conn, conn:
        for key, text, section in [('c1', FIRST, 'intro'), ('c2', SECOND, 'method')]:
            conn.execute('UPDATE chunks SET original_text=?, section_id=? WHERE chunk_id=?', (text, section, key))
    return db, PaperInsightService(SQLiteInsightStore(db))


def test_preview_is_deterministic_readonly_and_exactly_source_linked(source):
    db, service = source
    before = Path(db).read_bytes()
    draft = service.preview('ctx')
    assert draft == service.preview('ctx')
    assert draft.status == 'available'
    assert draft.source.version_id == 'v1' and draft.source.parse_revision_id == 'p1'
    assert repo.get_paper_insights(db, 'ctx') is None
    for item in draft.items:
        chunk = repo.get_chunk(db, item.chunk_id)
        assert chunk.original_text[item.start_offset:item.end_offset] == item.quote_original
        assert item.text == item.quote_original
        assert item.translation_revision_id is None
        assert item.producer == 'extractor' and item.content_kind == 'extracted'
        assert item.producer_version == draft.producer_version
    assert Path(db).read_bytes() == before


def test_explicit_save_roundtrip_and_existing_result_requires_replacement(source):
    db, service = source
    draft = service.preview('ctx')
    saved = service.save(draft)
    assert repo.get_paper_insights(db, 'ctx') == saved
    detail = SQLitePaperDetailReader(db).get('ctx', 'paper', 0, 20, None)
    assert detail['keywords'] and detail['important_sentences']
    assert detail['summary'] is None  # 요약 claim 재활용이 아니다.
    before = Path(db).read_bytes()
    with pytest.raises(PaperInsightValidationError):
        service.save(draft)
    assert Path(db).read_bytes() == before
    assert service.save(service.preview('ctx'), replace_existing=True).collection_id != saved.collection_id


def test_scope_and_old_parse_are_not_broadened(source):
    db, service = source
    repo.insert_parse_revision(db, ParseRevision('new', 'v1', 2))
    repo.insert_chunks(db, [Chunk('new-chunk', 'new', 0, SECOND.replace('Networks', 'Unrelated'))])
    draft = service.preview('ctx', 'intro')
    assert all(item.chunk_id == 'c1' for item in draft.items)
    assert 'Unrelated' not in ' '.join(item.text for item in service.preview('ctx').items)
    with pytest.raises(ResourceNotFoundError):
        service.preview('ctx', 'missing')
    with pytest.raises(ResourceNotFoundError):
        service.preview('missing')


def test_fragments_page_number_and_cross_chunk_condition_are_not_reconstructed(source):
    db, service = source
    left = 'Self-attention layers are faster than recurrent layers when the sequence\n6'
    right = 'length n is smaller than dimensionality d. Attention supports parallel computation across sequence positions.'
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute('UPDATE chunks SET original_text=? WHERE chunk_id=\'c1\'', (left,))
        conn.execute('UPDATE chunks SET original_text=? WHERE chunk_id=\'c2\'', (right,))
    draft = service.preview('ctx')
    assert draft.status == 'partial'
    assert len(draft.items) == 1
    assert draft.items[0].text == 'Attention supports parallel computation across sequence positions.'


def test_overlap_does_not_inflate_sentence_frequency(source):
    db, service = source
    text = 'Attention combines values through weighted sums.'
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute('UPDATE chunks SET original_text=?', (text,))
    draft = service.preview('ctx')
    assert draft.status == 'partial'
    assert len(draft.items) == 1 and draft.items[0].chunk_id == 'c1'


def test_references_use_stored_title_not_section_id_guessing(source):
    db, service = source
    repo.insert_sections(db, [PaperSection('intro', 'p1', 'Introduction', 1, 1, 1, 1),
                              PaperSection('method', 'p1', 'References', 2, 1, 2, 2)])
    assert all(item.chunk_id == 'c1' for item in service.preview('ctx').items)


@pytest.mark.parametrize('with_existing', [False, True])
def test_source_changes_are_rejected_without_replacing_existing_result(source, with_existing):
    db, service = source
    saved = service.save(service.preview('ctx')) if with_existing else None
    draft = service.preview('ctx')
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE chunks SET original_text='Changed.' WHERE chunk_id='c1'")
    before = Path(db).read_bytes()
    with pytest.raises(DataIntegrityError):
        service.save(draft, replace_existing=True)
    assert Path(db).read_bytes() == before
    with closing(sqlite3.connect(db)) as conn:
        row = conn.execute('SELECT collection_id FROM paper_insight_collections').fetchone()
        assert row == (saved.collection_id,) if saved else row is None


def test_empty_preview_does_not_erase_existing_result(source):
    db, service = source
    saved = service.save(service.preview('ctx'))
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE chunks SET original_text='incomplete trailing fragment'")
    draft = service.preview('ctx')
    assert draft.status == 'not_available'
    with pytest.raises(PaperInsightValidationError):
        service.save(draft, replace_existing=True)
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute('SELECT collection_id FROM paper_insight_collections').fetchone()[0] == saved.collection_id


def test_policy_and_preview_versions_cannot_be_mislabelled(source):
    db, service = source
    draft = service.preview('ctx')
    with pytest.raises(PaperInsightValidationError):
        service.save(replace(draft, producer_version='other'))
    changed = replace(draft.items[0], producer_version='other')
    with pytest.raises(PaperInsightValidationError):
        service.save(replace(draft, items=(changed,)))
    assert repo.get_paper_insights(db, 'ctx') is None


def test_source_linked_but_relabelled_item_is_not_an_extractor_result(source):
    db, service = source
    draft = service.preview('ctx')
    # 원문 구간이 유효해도 실제 선정 결과를 변조해 저장하지 않는다.
    forged = replace(draft, items=draft.items[:1])
    with pytest.raises(PaperInsightValidationError):
        service.save(forged)
    assert repo.get_paper_insights(db, 'ctx') is None


@pytest.mark.parametrize('with_existing', [False, True])
def test_competing_save_between_check_and_write_cannot_be_overwritten(source, monkeypatch, with_existing):
    db, service = source
    if with_existing:
        service.save(service.preview('ctx'))
    draft = service.preview('ctx')
    actual_save = repo.save_paper_insights
    competing = []

    def concurrent_save(db_path, context_id, scope, items, **conditions):
        competing.append(actual_save(db_path, context_id, scope, items))
        return actual_save(db_path, context_id, scope, items, **conditions)

    monkeypatch.setattr(repo, 'save_paper_insights', concurrent_save)
    with pytest.raises(PaperInsightConflictError):
        service.save(draft, replace_existing=with_existing)
    assert repo.get_paper_insights(db, 'ctx') == competing[0]


def test_competing_result_since_preview_is_not_overwritten_even_with_replace(source):
    db, service = source
    draft = service.preview('ctx')
    competing = repo.save_paper_insights(db, 'ctx', 'paper', list(draft.items))
    before = Path(db).read_bytes()
    with pytest.raises(PaperInsightConflictError):
        service.save(draft, replace_existing=True)
    assert repo.get_paper_insights(db, 'ctx') == competing
    assert Path(db).read_bytes() == before


def test_context_changes_inside_save_boundary_are_detected(source, monkeypatch):
    db, service = source
    draft = service.preview('ctx')
    actual_save = repo.save_paper_insights

    def changed_context(db_path, context_id, scope, items, **conditions):
        with closing(sqlite3.connect(db_path)) as conn, conn:
            conn.execute("UPDATE learning_contexts SET embedding_set_id='changed'")
        return actual_save(db_path, context_id, scope, items, **conditions)

    monkeypatch.setattr(repo, 'save_paper_insights', changed_context)
    with pytest.raises(DataIntegrityError):
        service.save(draft)
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM paper_insight_collections').fetchone()[0] == 0


def test_source_snapshot_is_immutable_and_unselected_changes_invalidate_preview(source):
    from dataclasses import FrozenInstanceError
    db, _ = source
    service = PaperInsightService(SQLiteInsightStore(db), ExtractionPolicy(max_sentences=1))
    draft = service.preview('ctx')
    with pytest.raises(FrozenInstanceError):
        draft.source.chunks[0].original_text = 'changed'
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE chunks SET original_text=original_text || ' Additional complete sentence changes the ranking.' WHERE chunk_id='c2'")
    with pytest.raises(DataIntegrityError):
        service.save(draft)
    assert repo.get_paper_insights(db, 'ctx') is None


def test_unicode_offsets_and_original_whitespace_are_preserved(source):
    db, service = source
    text = '😀 prefix.\nAttention  compares queries\nwith keys to select relevant values. Attention combines relevant values using weighted sums.'
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute('UPDATE chunks SET original_text=?', (text,))
    draft = service.preview('ctx')
    sentence = next(i for i in draft.items if '\nwith keys' in i.text)
    assert sentence.text == 'Attention  compares queries\nwith keys to select relevant values.'
    assert sentence.start_offset == len('😀 prefix.\n')
    assert text[sentence.start_offset:sentence.end_offset] == sentence.text


def test_context_change_after_preview_cannot_be_saved(source):
    db, service = source
    draft = service.preview('ctx')
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE learning_contexts SET version_id='wrong'")
    before = Path(db).read_bytes()
    with pytest.raises(DataIntegrityError):
        service.save(draft)
    assert Path(db).read_bytes() == before


@pytest.mark.parametrize('args', [{'max_keywords': True}, {'max_sentences': 0}, {'min_sentence_frequency': 11}])
def test_invalid_policy(args):
    with pytest.raises(PaperInsightValidationError):
        ExtractionPolicy(**args)


def test_absent_database_is_not_created(tmp_path):
    path = tmp_path / 'absent.sqlite'
    with pytest.raises(sqlite3.OperationalError):
        PaperInsightService(SQLiteInsightStore(str(path))).preview('ctx')
    assert not path.exists()


def test_corrupt_context_fails_closed(source):
    db, service = source
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE learning_contexts SET embedding_set_id='wrong'")
    with pytest.raises(DataIntegrityError):
        service.preview('ctx')


def test_cli_preview_and_save_are_explicit_and_do_not_print_content(source, capsys):
    db, _ = source
    args = ['--db', db, '--context-id', 'ctx']
    before = Path(db).read_bytes()
    assert main(args) == 0
    assert Path(db).read_bytes() == before
    assert main([*args, '--save']) == 0
    assert main([*args, '--save']) == 1
    captured = capsys.readouterr()
    assert 'Attention' not in captured.out + captured.err
    assert db not in captured.out + captured.err
    assert 'INSIGHT_EXTRACTION_FAILED' in captured.err
