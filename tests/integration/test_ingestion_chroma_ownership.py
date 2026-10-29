"""수집이 직접 만든 Chroma 클라이언트만 닫고 주입된 클라이언트는 닫지 않는지 검증한다."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from tests.fixtures.pdf_builder import write_minimal_pdf
from tests.integration.test_ingestion import (
    FixtureProvider, _fake_embed_texts, _fake_embedding_dimension,
)
from solo_leveling.application.translation.service import TranslationService
from solo_leveling.domain.translation import TranslationSettings
from solo_leveling.infrastructure.storage import vector_store
from solo_leveling.infrastructure.storage.vector_store import ChromaCloseResult
from solo_leveling.workers import ingestion as ingestion_module


@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch):
    monkeypatch.setattr(ingestion_module, 'embed_texts', _fake_embed_texts)
    monkeypatch.setattr(ingestion_module, 'embedding_dimension', _fake_embedding_dimension)


@pytest.fixture
def created(monkeypatch):
    """수집이 get_client로 만든 클라이언트를 기록하고, 테스트가 끝나면 모두 닫는다."""
    clients = []
    real = vector_store.get_client

    def tracking(path):
        client = real(path)
        clients.append(client)
        return client

    monkeypatch.setattr(ingestion_module, 'get_client', tracking)
    yield clients
    for client in clients:
        vector_store.close_client(client)


def _run(tmp_path, **kwargs):
    pdf = tmp_path / 'paper.pdf'
    if not pdf.exists():
        write_minimal_pdf(str(pdf), ['First page of the paper.'])
    return ingestion_module.register_and_ingest(
        str(tmp_path / 'data.sqlite'), str(tmp_path / 'chroma'), str(pdf), paper_id='p',
        translation_service=TranslationService(FixtureProvider()),
        translation_settings=TranslationSettings('fake', 'fixture', 'prompt1'), **kwargs)


def _is_closed(client):
    return getattr(client, vector_store._CLOSED_MARK, False) is True


def test_owned_client_is_closed_after_success(tmp_path, created):
    assert _run(tmp_path)['status'] == 'ready'
    assert len(created) == 1 and _is_closed(created[0])


def test_owned_client_is_closed_after_failure_and_original_error_survives(tmp_path, created, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError('SYNTHETIC-SECRET original failure')

    monkeypatch.setattr(ingestion_module, 'verify_chunk_embeddings', fail)
    with pytest.raises(ValueError, match='original failure'):
        _run(tmp_path)
    assert len(created) == 1 and _is_closed(created[0])


def test_injected_client_is_borrowed_not_closed(tmp_path, created):
    import chromadb
    injected = chromadb.PersistentClient(path=str(tmp_path / 'chroma'))
    try:
        assert _run(tmp_path, chroma_client=injected)['status'] == 'ready'
        assert created == []                      # 직접 만들지 않았다
        assert not _is_closed(injected)           # 빌린 것은 닫지 않았다
        injected.heartbeat()                      # 여전히 사용 가능하다
    finally:
        vector_store.close_client(injected)


def test_injected_client_survives_failure_too(tmp_path, created, monkeypatch):
    import chromadb
    injected = chromadb.PersistentClient(path=str(tmp_path / 'chroma'))

    def fail(*args, **kwargs):
        raise ValueError('boom')

    monkeypatch.setattr(ingestion_module, 'verify_chunk_embeddings', fail)
    try:
        with pytest.raises(ValueError):
            _run(tmp_path, chroma_client=injected)
        assert created == [] and not _is_closed(injected)
        injected.heartbeat()
    finally:
        vector_store.close_client(injected)


@pytest.mark.parametrize('result', [ChromaCloseResult.FAILED, ChromaCloseResult.UNSUPPORTED])
def test_close_failure_does_not_mask_success_and_emits_only_a_code(tmp_path, created, monkeypatch, capsys, result):
    monkeypatch.setattr(ingestion_module, 'close_client', lambda client: result)
    assert _run(tmp_path)['status'] == 'ready'
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line.startswith('{')]
    assert {'event': 'chroma_close', 'code': result.value} in events


def test_close_failure_does_not_mask_original_error(tmp_path, created, monkeypatch):
    monkeypatch.setattr(ingestion_module, 'close_client', lambda client: ChromaCloseResult.FAILED)

    def fail(*args, **kwargs):
        raise ValueError('original failure')

    monkeypatch.setattr(ingestion_module, 'verify_chunk_embeddings', fail)
    with pytest.raises(ValueError, match='original failure'):
        _run(tmp_path)


def test_no_client_is_created_when_index_stage_is_never_reached(tmp_path, created):
    pdf = tmp_path / 'paper.pdf'
    write_minimal_pdf(str(pdf), ['First page.'])
    result = ingestion_module.register_and_ingest(
        str(tmp_path / 'data.sqlite'), str(tmp_path / 'chroma'), str(pdf), paper_id='p',
        translation_service=TranslationService(FixtureProvider(fail=True)),
        translation_settings=TranslationSettings('fake', 'fixture', 'prompt1'))
    assert result['status'] == 'failed'
    assert created == []
