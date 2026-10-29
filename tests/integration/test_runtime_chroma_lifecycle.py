"""실제 Chroma에서 서버 종료·외부 클라이언트 보존·반복 파일 수를 확인한다."""

import asyncio
import sys
from pathlib import Path
import threading
from dataclasses import replace

import pytest

from solo_leveling.interfaces.mcp import runtime
from solo_leveling.infrastructure.storage.vector_store import get_client, close_client


def test_repeated_runtime_shutdown_preserves_other_client(tmp_path, monkeypatch):
    real_get_client = get_client
    owned = []

    def create_client(path):
        client = real_get_client(path)
        owned.append(client)
        return client

    monkeypatch.setattr(runtime, 'get_client', create_client)
    monkeypatch.setattr(runtime, 'create_server', lambda services, *, lifespan: (services, lifespan))
    outside = real_get_client(str(tmp_path / 'chroma'))
    outside.get_or_create_collection('surviving-collection')
    fd_path = Path('/dev/fd' if sys.platform == 'darwin' else '/proc/self/fd')
    counts = []
    try:
        for _ in range(6):
            services, lifespan = runtime.build_runtime_server({'DATA_ROOT': str(tmp_path),
                'OPENAI_API_KEY': 'synthetic', 'LOCAL_ONLY': 'true', 'ALLOW_EXTERNAL_API': 'false'})
            assert services.ingestion.target.services.chroma_client is owned[-1]
            assert services.ingestion.list_jobs() == {'jobs': [], 'next_cursor': None}
            async def run():
                async with lifespan(None):
                    assert outside.get_collection('surviving-collection').count() == 0
            asyncio.run(run())
            assert getattr(owned[-1], '_solo_leveling_closed') is True
            assert outside.get_collection('surviving-collection').count() == 0
            if fd_path.is_dir():
                counts.append(len(list(fd_path.iterdir())))
        # 최초 실행의 초기화 효과를 제외하고 반복 종료 후 누적을 검사한다.
        if counts:
            assert max(counts[1:]) - min(counts[1:]) <= 3, counts
        print('종료 후 열린 파일 수:', counts)
    finally:
        close_client(outside)
        for client in owned:
            close_client(client)


def test_shutdown_during_real_ingestion_and_search(tmp_path, monkeypatch):
    from tests.fixtures.pdf_builder import write_minimal_pdf
    from tests.integration.test_ingestion import FixtureProvider, _fake_embed_texts
    from solo_leveling.application.translation.service import TranslationService
    from solo_leveling.domain.translation import TranslationSettings
    from solo_leveling.infrastructure.database import repository as repo
    from solo_leveling.interfaces.mcp.ingestion import IngestionShuttingDownError

    translating, release_ingestion = threading.Event(), threading.Event()
    searching, release_search = threading.Event(), threading.Event()
    closed = threading.Event()

    class Provider(FixtureProvider):
        def translate(self, request, settings):
            if len(self.calls) == 1:
                translating.set()
                assert release_ingestion.wait(5)
            return super().translate(request, settings)

    class Embedder:
        def __call__(self, texts, *, model_name):
            assert not closed.is_set()
            if texts == ['shutdown-query']:
                searching.set()
                assert release_search.wait(5)
            assert not closed.is_set()
            return _fake_embed_texts(texts)

        def close(self):
            closed.set()

    original_build = runtime.build_services
    provider = Provider()

    def build(*args, **kwargs):
        return replace(original_build(*args, **kwargs),
            translation=TranslationService(provider),
            translation_settings=TranslationSettings('fake', 'fixture', 'prompt1'))

    monkeypatch.setattr(runtime, 'build_services', build)
    monkeypatch.setattr(runtime, 'ProcessEmbedder', lambda *args, **kwargs: Embedder())
    monkeypatch.setattr(runtime, 'create_server', lambda services, *, lifespan: (services, lifespan))
    services, lifespan = runtime.build_runtime_server({'DATA_ROOT': str(tmp_path),
        'OPENAI_API_KEY': 'synthetic', 'MCP_SHUTDOWN_TIMEOUT_SECONDS': '0'})
    manager = services.ingestion.target
    client = manager.services.chroma_client
    # 실제 등록·벡터 검색을 사용하되 번역과 모델만 고정된 테스트 대역으로 교체한다.
    search = services.answer.gate.wrap(services.answer.target.search)
    for name in ('first', 'second', 'queued'):
        write_minimal_pdf(str(tmp_path / 'imports' / f'{name}.pdf'), [f'{name} attention paper.'])
    workers, results, errors = [], [], []
    try:
        first = services.ingestion.add_local_pdf('first.pdf', 'first')
        assert manager.wait_for_idle(5)
        assert manager.get_status(first['job_id'])['status'] == 'ready'
        assert search.search('baseline', version_id=first['version_id']).result.items
        second = services.ingestion.add_local_pdf('second.pdf', 'second')
        assert translating.wait(5)
        queued = services.ingestion.add_local_pdf('queued.pdf', 'queued')

        def request():
            try:
                results.append(search.search('shutdown-query', version_id=first['version_id']))
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=request)
        workers.append(worker)
        worker.start()
        assert searching.wait(5)

        async def stop():
            async with lifespan(None):
                pass
        asyncio.run(stop())
        with pytest.raises(IngestionShuttingDownError):
            services.ingestion.list_jobs()
        assert not closed.is_set()
        assert not getattr(client, '_solo_leveling_closed', False)
        release_search.set()
        worker.join(5)
        assert not errors and results[0].result.items
        assert not closed.is_set()
        release_ingestion.set()
        assert manager.wait_for_idle(5)
        assert manager.get_status(second['job_id'])['status'] == 'ready'
        cancelled = repo.get_job(manager.db_path, queued['job_id'])
        assert cancelled.status.value == 'interrupted'
        assert 'SERVER_SHUTTING_DOWN' in cancelled.limitations
    finally:
        release_search.set()
        release_ingestion.set()
        for worker in workers:
            worker.join(5)
        manager.begin_shutdown()
        assert manager.wait_for_idle(5)
        for thread in threading.enumerate():
            if thread.name == 'mcp-resource-cleanup':
                thread.join(5)
                assert not thread.is_alive()
        runtime_closed = getattr(client, '_solo_leveling_closed', False)
        close_client(client)
    assert closed.is_set()
    assert runtime_closed is True
