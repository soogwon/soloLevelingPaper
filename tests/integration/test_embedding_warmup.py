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

    def embedder(texts, *, model_name):
        calls.append((texts, model_name))
        return [[1., 0.]]

    thread = start_warmup(embedder, db)
    thread.join(5)
    assert not thread.is_alive()
    assert calls == [([WARMUP_TEXT], 'saved-model')]


def test_warmup_failure_does_not_escape(setup_search, capsys):
    db, *_ = setup_search

    def embedder(texts, *, model_name):
        raise RuntimeError('SYNTHETIC_PRIVATE_FAILURE')

    thread = start_warmup(embedder, db)
    thread.join(5)
    assert not thread.is_alive()
    assert 'SYNTHETIC_PRIVATE_FAILURE' not in capsys.readouterr().err
