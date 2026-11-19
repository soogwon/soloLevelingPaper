"""로컬 질의·색인 입력도 특수 토큰을 포함한 한도를 넘으면 encode 전에 거부한다."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from solo_leveling.infrastructure.embeddings import embedder
from solo_leveling.infrastructure.embeddings.token_windows import TokenWindowError


def model(monkeypatch):
    tokenizer = Mock()
    tokenizer.encode.side_effect = lambda text, **kwargs: [1] * (len(text.split()) + 2)
    fake = SimpleNamespace(tokenizer=tokenizer, max_seq_length=128,
                           encode=Mock(return_value=[SimpleNamespace(tolist=lambda: [1.0])]))
    monkeypatch.setattr(embedder, '_get_model', lambda *args, **kwargs: fake)
    return fake


def test_exact_limit_is_encoded_without_changing_input(monkeypatch):
    fake = model(monkeypatch)
    text = ' '.join(['synthetic'] * 126)
    assert embedder.embed_texts([text]) == [[1.0]]
    fake.tokenizer.encode.assert_called_once_with(text, add_special_tokens=True, truncation=False)
    fake.encode.assert_called_once_with([text], convert_to_numpy=True)


def test_later_long_chunk_blocks_entire_batch_before_encode(monkeypatch, capsys):
    fake = model(monkeypatch)
    with pytest.raises(TokenWindowError, match='^EMBEDDING_INPUT_TOO_LONG$'):
        embedder.embed_texts(['short', ' '.join(['PRIVATE_SOURCE'] * 127)])
    fake.encode.assert_not_called()
    assert 'PRIVATE_SOURCE' not in capsys.readouterr().err


def test_long_query_is_not_silently_truncated(monkeypatch):
    fake = model(monkeypatch)
    with pytest.raises(TokenWindowError, match='^EMBEDDING_INPUT_TOO_LONG$'):
        embedder.embed_texts([' '.join(['question'] * 129)], local_files_only=True)
    fake.encode.assert_not_called()


def test_tokenizer_failure_is_redacted(monkeypatch):
    fake = model(monkeypatch)
    fake.tokenizer.encode.side_effect = RuntimeError('PRIVATE_SOURCE')
    with pytest.raises(TokenWindowError, match='^EMBEDDING_TOKENIZATION_FAILED$'):
        embedder.embed_texts(['source'])
    fake.encode.assert_not_called()


def test_unknown_model_limit_does_not_guess(monkeypatch):
    fake = model(monkeypatch)
    fake.max_seq_length = None
    with pytest.raises(TokenWindowError, match='^EMBEDDING_WINDOW_POLICY_INVALID$'):
        embedder.embed_texts(['source'])
    fake.encode.assert_not_called()
