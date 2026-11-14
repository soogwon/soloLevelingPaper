"""MCP 시작 시 임베딩 worker 준비(#119)."""

from solo_leveling.infrastructure.database.schema import init_db
from solo_leveling.infrastructure.embeddings.warmup import (
    WARMUP_TEXT, default_index_model, start_warmup,
)
from tests.integration.test_scoped_search import setup_search  # noqa: F401 - fixture 재사용


def test_no_index_means_no_warmup(tmp_path):
    db = str(tmp_path / 'db.sqlite')
    init_db(db)
    calls = []
    assert default_index_model(db) is None
    assert start_warmup(lambda texts, *, model_name: calls.append(model_name), db) is None
    assert calls == []


def test_unreadable_db_means_no_warmup(tmp_path):
    assert default_index_model(str(tmp_path / 'missing' / 'db.sqlite')) is None


def test_warms_with_published_index_model_and_fixed_text(setup_search):
    db, *_ = setup_search
    calls = []

    def embedder(texts, *, model_name, local_files_only):
        calls.append((texts, model_name, local_files_only))
        return [[1., 0.]]

    thread = start_warmup(embedder, db)
    thread.join(5)
    assert not thread.is_alive()
    assert calls == [([WARMUP_TEXT], 'saved-model', True)]


def test_warmup_failure_does_not_escape(setup_search, capsys):
    db, *_ = setup_search

    def embedder(texts, *, model_name, local_files_only):
        raise RuntimeError('SYNTHETIC_PRIVATE_FAILURE')

    thread = start_warmup(embedder, db)
    thread.join(5)
    assert not thread.is_alive()
    assert 'SYNTHETIC_PRIVATE_FAILURE' not in capsys.readouterr().err


def cache_policy_worker(connection):
    """실제 worker 경로에서 모델 생성만 대체해 네트워크 없이 캐시 정책을 검증한다."""
    import sys
    from types import SimpleNamespace
    from solo_leveling.infrastructure.embeddings import embedder
    from solo_leveling.infrastructure.embeddings.worker import run_worker

    class Model:
        def __init__(self, name, *, local_files_only=False):
            if name == 'missing' and local_files_only:
                raise FileNotFoundError('SYNTHETIC_PRIVATE_CACHE_PATH')
            self.offline = local_files_only

        def encode(self, texts, **kwargs):
            return [SimpleNamespace(tolist=lambda: [float(self.offline)]) for _ in texts]

        def get_embedding_dimension(self):
            return 1

    embedder._model_cache.clear()
    sys.modules['sentence_transformers'] = SimpleNamespace(SentenceTransformer=Model)
    run_worker(connection)


def test_cached_warmup_passes_offline_policy_through_worker_and_reuses_model(tmp_path):
    from solo_leveling.infrastructure.embeddings.process_embedder import ProcessEmbedder
    embedder = ProcessEmbedder(10, worker_target=cache_policy_worker, log_dir=tmp_path)
    try:
        assert embedder([WARMUP_TEXT], model_name='cached', local_files_only=True) == [[1.]]
        # 일반 질문도 워밍업이 로딩한 동일 모델을 그대로 사용한다.
        assert embedder(['question'], model_name='cached') == [[1.]]
    finally:
        embedder.close()


def test_missing_cache_warmup_is_skipped_and_normal_request_recovers(tmp_path, monkeypatch, capsys):
    from solo_leveling.infrastructure.embeddings import warmup
    from solo_leveling.infrastructure.embeddings.process_embedder import ProcessEmbedder
    monkeypatch.setattr(warmup, 'default_index_model', lambda _: 'missing')
    embedder = ProcessEmbedder(10, worker_target=cache_policy_worker, log_dir=tmp_path)
    try:
        thread = start_warmup(embedder, 'synthetic-db')
        thread.join(15)
        assert not thread.is_alive()
        # 캐시 누락 실패는 준비 단계에만 적용하고 후속 일반 요청은 다시 실행한다.
        assert embedder._process is None
        assert embedder(['question'], model_name='missing') == [[0.]]
    finally:
        embedder.close()
    assert 'SYNTHETIC_PRIVATE_CACHE_PATH' not in capsys.readouterr().err
