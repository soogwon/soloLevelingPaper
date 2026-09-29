"""MCP 등록 진입점이 주입된 임베더만 사용하고 실패 상태를 보존하는지 검증한다."""

import pytest
from solo_leveling.workers import ingestion
from solo_leveling.application.translation.service import TranslationService
from solo_leveling.domain.translation import TranslationSettings
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.embeddings.process_embedder import EmbeddingProcessError, EmbeddingFailure
from tests.fixtures.pdf_builder import write_minimal_pdf
from tests.integration.test_ingestion import FixtureProvider


@pytest.mark.parametrize('fail', [False, True])
def test_prepared_ingestion_uses_injected_embedder(tmp_path, monkeypatch, fail):
    pdf = tmp_path / 'sample.pdf'
    write_minimal_pdf(str(pdf), ['Attention.'])
    db = str(tmp_path / 'db.sqlite')
    prepared = ingestion.prepare_local_ingestion(db, str(tmp_path / 'chroma'), str(pdf))

    def forbidden(*args, **kwargs):
        pytest.fail('부모 프로세스에서 직접 임베딩을 호출했습니다.')

    monkeypatch.setattr(ingestion, 'embed_texts', forbidden)
    monkeypatch.setattr(ingestion, 'embedding_dimension', forbidden)

    def embed(texts, *, model_name):
        if fail:
            raise EmbeddingProcessError(EmbeddingFailure.TIMEOUT)
        return [[1.0, 0.0] for _ in texts]

    options = dict(translation_service=TranslationService(FixtureProvider()),
                   translation_settings=TranslationSettings('fake', 'fixture', 'prompt1'), embedder=embed)
    if fail:
        with pytest.raises(EmbeddingProcessError):
            ingestion.run_prepared_local_ingestion(prepared, **options)
        job = repo.get_job(db, prepared.registration.job_id)
        assert job.status.value == 'failed'
        assert 'EMBEDDING_TIMEOUT' in job.limitations
        assert repo.get_search_index(db, prepared.registration.version_id) is None
    else:
        assert ingestion.run_prepared_local_ingestion(prepared, **options)['status'] == 'ready'
