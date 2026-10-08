"""진단 로그의 단계·시간·요청 격리와 비밀정보 비노출을 검증한다."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import anyio
import pytest

from solo_leveling.diagnostics import stage, traced


def records(capsys):
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'SECRET' not in captured.err
    return [json.loads(line) for line in captured.err.splitlines()]


def test_success_and_failure(capsys):
    @traced('answer', request=True)
    def run(secret):
        with stage('index_lookup'):
            pass
        with stage('generation_api_call'):
            raise ValueError(secret)

    with pytest.raises(ValueError, match='SECRET'):
        run('SECRET')
    logs = records(capsys)
    assert len({r['request_id'] for r in logs}) == 1
    assert [r['event'] for r in logs] == ['start', 'start', 'end', 'start', 'error', 'error']
    assert all(r['elapsed_ms'] >= 0 for r in logs)
    assert logs[-1]['error_code'] == 'STAGE_FAILED'
    assert set(logs[0]) == {'request_id', 'span_id', 'stage', 'event', 'elapsed_ms', 'error_code'}


def test_anyio_thread_propagation(capsys):
    @traced('answer', request=True)
    def worker():
        with stage('evidence_save'):
            pass

    @traced('ask_paper', request=True)
    async def call():
        await anyio.to_thread.run_sync(worker)

    anyio.run(call)
    logs = records(capsys)
    assert len({r['request_id'] for r in logs}) == 1
    assert logs[0]['stage'] == logs[-1]['stage'] == 'ask_paper'


def test_concurrent_requests_and_repeated_stages(capsys):
    barrier = Barrier(2)

    @traced('answer', request=True)
    def run():
        barrier.wait(timeout=5)
        for _ in range(2):
            with stage('query_embedding'):
                pass

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: run(), range(2)))
    logs = records(capsys)
    assert len({r['request_id'] for r in logs}) == 2
    assert len({r['span_id'] for r in logs}) == 6


def test_no_active_request_and_output_failure(capsys, monkeypatch):
    with stage('index_lookup'):
        pass
    assert records(capsys) == []

    class BrokenStream:
        def write(self, value):
            raise OSError('SECRET')

    monkeypatch.setattr('sys.stderr', BrokenStream())

    @traced('answer', request=True)
    def run():
        return 42

    assert run() == 42


def test_model_load_and_encode_are_separate(capsys, monkeypatch):
    import sys
    from types import SimpleNamespace
    from solo_leveling.infrastructure.embeddings import embedder

    class Vector:
        def tolist(self):
            return [1.0]

    class Model:
        def encode(self, texts, **kwargs):
            return [Vector()]

    monkeypatch.setattr(embedder, '_model_cache', {})
    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(SentenceTransformer=lambda name: Model()))

    @traced('answer', request=True)
    def run():
        return embedder.embed_texts(['SECRET'], model_name='SECRET-model')

    assert run() == [[1.0]]
    logs = records(capsys)
    assert [r['stage'] for r in logs if r['event'] == 'start'] == [
        'answer', 'embedding_model_load', 'embedding_library_import',
        'embedding_model_construct', 'embedding_encode']
    assert run() == [[1.0]]
    cached = records(capsys)
    assert [r['stage'] for r in cached if r['event'] == 'start'] == [
        'answer', 'embedding_model_load', 'embedding_memory_cache_hit', 'embedding_encode']


def test_model_construction_failure_is_not_cached(capsys, monkeypatch):
    import sys
    from types import SimpleNamespace
    from solo_leveling.infrastructure.embeddings import embedder

    def fail(name):
        raise RuntimeError('SECRET')

    monkeypatch.setattr(embedder, '_model_cache', {})
    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(SentenceTransformer=fail))

    @traced('answer', request=True)
    def run():
        embedder.embed_texts(['SECRET'], model_name='SECRET-model')

    with pytest.raises(RuntimeError):
        run()
    logs = records(capsys)
    assert any(r['stage'] == 'embedding_library_import' and r['event'] == 'end' for r in logs)
    assert any(r['stage'] == 'embedding_model_construct' and r['event'] == 'error' for r in logs)
    assert not embedder._model_cache


def test_evidence_trace_is_fixed_metadata_and_best_effort(capsys, monkeypatch):
    from solo_leveling.diagnostics import evidence_diagnostic
    evidence_diagnostic('candidate', attempt_id='a1')
    assert records(capsys) == []

    @traced('answer', request=True)
    def run():
        evidence_diagnostic('candidate', attempt_id='a1', chunk_id='SECRET body/path',
                            pdf_page='SECRET page', supplemental='SECRET', evidence_id='e1')
        evidence_diagnostic('quality', attempt_id='a1', reason_code='SECRET reason')
        return 42

    assert run() == 42
    logs = records(capsys)
    trace = [r for r in logs if r['stage'] == 'evidence_trace']
    assert len(trace) == 1
    assert trace[0]['chunk_id'] is trace[0]['pdf_page'] is trace[0]['supplemental'] is None
    assert trace[0]['evidence_id'] == 'e1'

    class BrokenStream:
        def write(self, value):
            raise OSError('SECRET')

    monkeypatch.setattr('sys.stderr', BrokenStream())
    assert run() == 42
