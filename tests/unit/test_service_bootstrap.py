"""서비스 조립과 저장 근거 조회를 외부 호출 없이 검증한다."""

import pytest

from solo_leveling.application.evidence_qa.errors import InvalidArgumentError, ResourceNotFoundError, DataIntegrityError
from solo_leveling.application.evidence_qa.serialization import serialize_stored_evidence_result
from solo_leveling.application.evidence_qa.evidence import EvidenceService
from solo_leveling.domain.evidence_qa import GetEvidenceResult
from solo_leveling.domain.context import ContextNotReadyError
from solo_leveling.domain.models import Evidence, LearningContext
from solo_leveling.infrastructure.bootstrap import build_services
from solo_leveling.infrastructure.generation.openai_generator import OpenAIGenerationSettings
from solo_leveling.infrastructure.translation.openai_provider import OpenAITranslationConfig
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection
from contextlib import closing
from tests.unit.test_learning_persistence import setup_db, publish


def services(db):
    return build_services(db, object(), generation=OpenAIGenerationSettings('test-key'),
                          translation=OpenAITranslationConfig('test-key'))


def test_build_has_no_io(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('조립 중 외부 호출이 발생했습니다.')
    monkeypatch.setattr('requests.post', forbidden)
    db = tmp_path / 'absent.sqlite'
    bundle = services(str(db))
    assert not db.exists()
    assert bundle.answer.search is bundle.search
    assert bundle.translation.provider.config.translation_settings == bundle.translation_settings


def test_stored_evidence_without_search(setup_db):
    db, chunks = setup_db
    publish(db, chunks)
    repo.save_evidences(db, 'ctx', [Evidence('e1', 'c1', '첫째.', 'First.')])
    # 검색 클라이언트가 없어도 새 서비스에서 저장 근거를 조회한다.
    result = services(db).evidence.get('ctx', ['e1'])
    data = serialize_stored_evidence_result(result)
    assert data['evidence'][0]['file_display_name'] == 'paper.pdf'
    assert data['evidence'][0]['quote_ko'] == '첫째.'
    assert '/private' not in str(data)
    with pytest.raises(ResourceNotFoundError):
        services(db).evidence.get('missing', ['e1'])
    with pytest.raises(ResourceNotFoundError):
        services(db).evidence.get('ctx', ['missing'])
    repo.create_learning_context(db, LearningContext('other', 'v1', 'p1', 't1', embedding_set_id='idx'))
    with pytest.raises(ResourceNotFoundError):
        services(db).evidence.get('other', ['e1'])
    with closing(get_connection(db)) as conn, conn:
        conn.execute("UPDATE evidences SET quote_original='tampered'")
    with pytest.raises(DataIntegrityError):
        services(db).evidence.get('ctx', ['e1'])


@pytest.mark.parametrize('ids', [[], ['e', 'e'], 'e', [True], [None]])
def test_invalid_evidence_input(ids):
    with pytest.raises(InvalidArgumentError):
        services('unused').evidence.get('ctx', ids)


def test_search_error_types(setup_db):
    db, _ = setup_db
    bundle = services(db)
    with pytest.raises(ResourceNotFoundError):
        bundle.search.search('질문', context_id='missing')
    with pytest.raises(ResourceNotFoundError):
        bundle.search.search('질문', version_id='missing')
    with pytest.raises(ContextNotReadyError):
        bundle.search.search('질문', version_id='v1')
    with pytest.raises(InvalidArgumentError):
        bundle.answer.answer('질문', version_id='v1', top_k=True)
    with pytest.raises(InvalidArgumentError):
        bundle.search.search('', version_id='v1')


def test_reader_must_return_all_requested_ids():
    class MissingResultReader:
        def get(self, context_id, evidence_ids):
            return GetEvidenceResult(())

    with pytest.raises(DataIntegrityError):
        EvidenceService(MissingResultReader()).get('ctx', ['e1'])
