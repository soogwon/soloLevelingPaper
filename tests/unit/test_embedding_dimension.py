"""embedding_dimension이 새/옛 sentence-transformers 이름을 모두 지원하는지 확인한다."""

import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from solo_leveling.infrastructure.embeddings import embedder


class _NewModel:
    def get_embedding_dimension(self):
        return 384

    def get_sentence_embedding_dimension(self):
        warnings.warn('deprecated', FutureWarning)
        return -1


class _OldModel:
    def get_sentence_embedding_dimension(self):
        return 384


def test_uses_new_method_without_warning(monkeypatch):
    monkeypatch.setattr(embedder, '_get_model', lambda name: _NewModel())
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        assert embedder.embedding_dimension('fixture') == 384


def test_falls_back_to_old_method(monkeypatch):
    monkeypatch.setattr(embedder, '_get_model', lambda name: _OldModel())
    assert embedder.embedding_dimension('fixture') == 384
