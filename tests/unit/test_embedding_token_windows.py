"""모델·네트워크 없이 토큰 전체 보존과 기존 청크 연결을 확인한다."""

from types import SimpleNamespace

import pytest

from solo_leveling.infrastructure.embeddings.token_windows import (
    TokenWindowError, TokenWindowPolicy, require_fits, split_token_windows,
)


class Tokenizer:
    def encode(self, text, *, add_special_tokens, truncation):
        assert truncation is False
        ids = list(range(int(text)))
        return [1000, *ids, 1001] if add_special_tokens else ids

    def num_special_tokens_to_add(self, *, pair):
        assert pair is False
        return 2


def windows(count, **kwargs):
    return split_token_windows(Tokenizer(), str(count), parent_chunk_id='parent',
                              embedding_set_id='set', max_length=128, **kwargs)


@pytest.mark.parametrize('count', [1, 88, 112, 113, 126, 128, 200, 248, 500])
def test_every_token_is_preserved_and_parent_is_unchanged(count):
    result = windows(count)
    assert result[-1].token_end == count
    covered = {}
    for item in result:
        assert item.parent_chunk_id == 'parent' and item.embedding_set_id == 'set'
        assert len(item.token_ids) <= 112
        assert item.token_ids == tuple(range(item.token_start, item.token_end))
        covered.update(zip(range(item.token_start, item.token_end), item.token_ids))
    assert covered == dict(enumerate(range(count)))
    for previous, current in zip(result, result[1:]):
        assert previous.token_end - current.token_start == 24


def test_final_window_does_not_create_redundant_tail():
    assert [(w.token_start, w.token_end) for w in windows(200)] == [(0, 112), (88, 200)]


def test_fragment_identity_is_stable_but_set_and_policy_are_distinct():
    first = windows(248)
    assert first == windows(248)
    changed = split_token_windows(Tokenizer(), '248', parent_chunk_id='parent',
                                 embedding_set_id='new-set', max_length=128)
    assert first[0].embedding_fragment_id != changed[0].embedding_fragment_id
    assert first[0].embedding_fragment_id != windows(248, policy=TokenWindowPolicy(112, 16))[0].embedding_fragment_id
    assert TokenWindowPolicy().fingerprint()['stride_tokens'] == 88
    assert 'token_ids' not in repr(first[0])


@pytest.mark.parametrize('payload,overlap', [(0, 0), (True, 0), (112, -1), (112, 112), (112, True)])
def test_invalid_policy_rejected(payload, overlap):
    with pytest.raises(TokenWindowError):
        TokenWindowPolicy(payload, overlap)


def test_special_tokens_count_against_model_limit():
    require_fits(Tokenizer(), '126', 128)
    with pytest.raises(TokenWindowError, match='^EMBEDDING_INPUT_TOO_LONG$'):
        require_fits(Tokenizer(), '127', 128)
    with pytest.raises(TokenWindowError, match='POLICY_INVALID'):
        windows(127, policy=TokenWindowPolicy(127, 24))


def test_tokenizer_error_has_no_source_text():
    def fail(*args, **kwargs):
        raise RuntimeError('비공개 본문')
    with pytest.raises(TokenWindowError, match='^EMBEDDING_TOKENIZATION_FAILED$') as caught:
        require_fits(SimpleNamespace(encode=fail), '비공개 본문', 128)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize('ids', [None, [], [True], [-1], ['1']])
def test_invalid_tokenizer_results_rejected(ids):
    with pytest.raises(TokenWindowError):
        require_fits(SimpleNamespace(encode=lambda *a, **kw: ids), '합성', 128)
