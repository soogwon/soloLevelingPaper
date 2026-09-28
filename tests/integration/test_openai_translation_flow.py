"""OpenAI 통신·임베딩 계산을 대체하고 실제 PDF·SQLite·Chroma 연결을 검증한다."""

import json
from unittest.mock import Mock

import pytest
import requests

from solo_leveling.application.translation.service import TranslationService
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.translation.openai_provider import OpenAITranslationConfig, OpenAITranslationProvider
from solo_leveling.workers import ingestion
from tests.fixtures.pdf_builder import write_minimal_pdf


@pytest.mark.parametrize('mode', ['success', 'partial', 'failed'])
def test_pdf_registration_with_openai_provider(tmp_path, monkeypatch, mode):
    def post(url, **kwargs):
        source = json.loads(kwargs['json']['input'][0]['content'])['original_text'].strip()
        fail = mode == 'failed' or (mode == 'partial' and source == 'Results.')
        response = Mock(status_code=503 if fail else 200)
        translation = {'Attention.': '어텐션.', 'Results.': '결과.'}[source]
        response.json.return_value = {'status': 'completed', 'output': [
            {'type': 'message', 'role': 'assistant', 'status': 'completed', 'content': [
                {'type': 'output_text', 'text': json.dumps({'text': translation})}]}]}
        return response

    http = Mock(side_effect=post)
    monkeypatch.setattr(requests, 'post', http)
    monkeypatch.setattr(ingestion, 'embed_texts', lambda texts, **kwargs: [[1., 0.] for _ in texts])
    monkeypatch.setattr(ingestion, 'embedding_dimension', lambda model: 2)
    pdf = tmp_path / 'synthetic.pdf'
    write_minimal_pdf(str(pdf), ['Attention.', 'Results.'])
    db, chroma = str(tmp_path / 'test.sqlite'), str(tmp_path / 'chroma')
    config = OpenAITranslationConfig('test-key', allow_external_api=True)
    kwargs = dict(paper_id='paper', embedding_model='fixed-test',
        translation_service=TranslationService(OpenAITranslationProvider(config)),
        translation_settings=config.translation_settings)
    result = ingestion.register_and_ingest(db, chroma, str(pdf), **kwargs)
    assert http.call_count == 2
    chunks = repo.get_chunks_by_parse_revision(db, result['parse_revision_id'])
    assert [c.original_text.strip() for c in chunks] == ['Attention.', 'Results.']
    metadata = repo.get_translation_metadata(db, result['parse_revision_id'])
    assert metadata['provider'] == 'openai'
    assert metadata['model'] == config.translation_settings.model
    assert metadata['prompt_version'] == config.translation_settings.prompt_version
    expected = {'success': 2, 'partial': 1, 'failed': 0}[mode]
    assert sum(c.text is not None for c in chunks) == expected
    assert result['chunk_count'] == expected
    if mode == 'failed':
        assert result['status'] == 'failed'
        assert repo.get_search_index(db, result['version_id']) is None
    else:
        assert result['status'] == 'ready'
        assert repo.get_search_index(db, result['version_id'])['chunk_count'] == expected
        reused = ingestion.register_and_ingest(db, chroma, str(pdf), **kwargs)
        assert reused['reused_existing'] is True
        assert http.call_count == 2
    if mode == 'partial':
        assert any('translation_failed:' in value for value in result['limitations'])
