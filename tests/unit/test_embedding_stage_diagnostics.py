"""실제 라이브러리·모델·API 없이 임베딩 단계 계측과 안전 출력을 검증한다."""

import builtins
from io import StringIO
import json
import sys
from types import SimpleNamespace

import pytest

from solo_leveling.diagnostics import _request
from solo_leveling.infrastructure.embeddings import embedder
from solo_leveling.interfaces.cli.safe_diagnostic_stream import SafeDiagnosticStream


@pytest.mark.parametrize('failure', [None, 'import', 'construct', 'encode'])
def test_embedding_stages_are_distinct_and_do_not_log_content(monkeypatch, failure):
    output = StringIO()
    monkeypatch.setattr(sys, 'stderr', SafeDiagnosticStream(output))
    monkeypatch.setattr(embedder, '_model_cache', {})
    original_import = builtins.__import__
    calls = []

    class Model:
        def __init__(self, name):
            calls.append('construct')
            if failure == 'construct':
                raise RuntimeError('PRIVATE_MODEL_PATH_AND_KEY')

        def encode(self, texts, **kwargs):
            calls.append('encode')
            if failure == 'encode':
                raise RuntimeError('PRIVATE_QUESTION_AND_EXCEPTION')
            return [SimpleNamespace(tolist=lambda: [0.1, 0.2])]

        def get_embedding_dimension(self):
            return 2

    def fake_import(name, *args, **kwargs):
        if name == 'sentence_transformers':
            calls.append('import')
            if failure == 'import':
                raise ImportError('PRIVATE_IMPORT_PATH')
            return SimpleNamespace(SentenceTransformer=Model)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', fake_import)
    token = _request.set('a' * 32)
    try:
        if failure:
            with pytest.raises((ImportError, RuntimeError)):
                embedder.embed_texts(['PRIVATE_QUESTION'], model_name='PRIVATE_MODEL')
        else:
            assert embedder.embed_texts(['PRIVATE_QUESTION'], model_name='PRIVATE_MODEL') == [[0.1, 0.2]]
            assert embedder.embedding_dimension('PRIVATE_MODEL') == 2
    finally:
        _request.reset(token)

    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert records and 'PRIVATE' not in output.getvalue()
    assert all(set(r) == {'request_id', 'span_id', 'stage', 'event', 'elapsed_ms', 'error_code'}
               for r in records)
    assert all(r['elapsed_ms'] >= 0 for r in records)
    stages = {'import': 'embedding_library_import', 'construct': 'embedding_model_construct',
              'encode': 'embedding_encode'}
    if failure:
        assert any(r['stage'] == stages[failure] and r['event'] == 'error'
                   and r['error_code'] == 'STAGE_FAILED' for r in records)
        assert calls == ['import', 'construct', 'encode'][:['import', 'construct', 'encode'].index(failure) + 1]
    else:
        assert calls == ['import', 'construct', 'encode']
        for stage in (*stages.values(), 'embedding_memory_cache_hit'):
            assert any(r['stage'] == stage and r['event'] == 'end' for r in records)
